import sys
import os
import re
import shutil
import base64
import mimetypes
import requests
import json
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
import threading
import subprocess

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QProgressBar, QMessageBox, QLabel, QFileDialog, QHeaderView,
    QDialog, QFormLayout, QDialogButtonBox, QSpacerItem,
    QSizePolicy, QComboBox, QShortcut, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QFrame, QAbstractItemView, QSplitter, QTextEdit,
    QStyle, QAction, QSystemTrayIcon, QTreeView, QStyledItemDelegate, QItemDelegate
)
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QKeySequence, QIcon
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QEvent, QTimer

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from tqdm import tqdm
from pyzotero import zotero

from pynput import keyboard

if sys.platform == 'darwin':
    from AppKit import NSApp, NSApplication, NSApplicationActivationPolicyAccessory

def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = os.path.join(os.path.dirname(sys.executable), 'Resources')
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

CONFIG_FILE = resource_path('config/config.json')

# --- Worker Signals ---
class WorkerSignals(QObject):
    progress = pyqtSignal(int, int)     # (row, progress_value)
    title = pyqtSignal(int, str)        # (row, title)
    finished = pyqtSignal(int, str)     # (row, filepath)
    error = pyqtSignal(int, str)        # (row, error_message)


# --- Worker Class ---
class SavePageWorker:
    def __init__(self, row, url, args, signals, cancel_event):
        self.row = row  # 行号，用于更新表格中的对应项
        self.url = url
        self.args = args
        self.signals = signals
        self.cancel_event = cancel_event
        self.stages = [
            "(1/7) 转换 Arxiv url",
            "(2/7) 启动浏览器并加载扩展",
            "(3/7) 访问目标网页",
            "(4/7) 等待页面内容翻译完成",
            "(5/7) 页面内容已加载并解析",
            "(6/7) 下载并编码资源",
            "(7/7) 保存到 Zotero"
        ]

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise Exception("Cancel Task")
    
    def check_arxiv_date_and_modify_url(self, arxiv_url):
        # 支持的链接格式提示
        supported_formats = """
        支持的 Arxiv 链接格式：
        1. https://arxiv.org/abs/{YYMM}.{NNNNN}
        2. https://arxiv.org/html/{YYMM}.{NNNNN}
        3. https://arxiv.org/pdf/{YYMM}.{NNNNN}
        4. https://ar5iv.org/abs/{YYMM}.{NNNNN}
        5. https://ar5iv.labs.arxiv.org/html/{YYMM}.{NNNNN}
        6. arxiv.org/abs/{YYMM}.{NNNNN} (无 https 前缀)
        7. ar5iv.labs.arxiv.org/html/{YYMM}.{NNNNN} (无 https 前缀)
        8. arxiv:YYMM.NNNNN (Zotero 中的 Arxiv 链接格式)
        """

        # 如果没有 /，则可能是 Zotero 中的 Arxiv 链接格式
        if '/' not in arxiv_url:
            # 去除所有多余空格
            arxiv_url = arxiv_url.replace(' ', '')
            match = re.search(r'arxiv:(\d{4})\.(\d{5})', arxiv_url)
            if not match:
                print("无效的 Arxiv 链接格式")
                print(supported_formats)
                return None

            paper_year = int(match.group(1)[:2])
            paper_month = int(match.group(1)[2:4])
            arxiv_id = match.group(2)
            arxiv_url = f"https://arxiv.org/abs/{paper_year}{paper_month}.{arxiv_id}"

        else:
            # 补全无 http 前缀的链接
            if not arxiv_url.startswith('http'):
                arxiv_url = 'https://' + arxiv_url

            # 正则匹配不同格式的 Arxiv 链接
            match = re.search(r'https://(?:arxiv\.org|ar5iv\.labs\.arxiv\.org|ar5iv\.org|)/(abs|html|pdf)/(\d{4})\.(\d{5})', arxiv_url)
            if not match:
                print("无效的 Arxiv 链接格式")
                print(supported_formats)
                return None

            link_type = match.group(1)
            paper_year = int(match.group(2)[:2]) + 2000  # 转换为完整年份
            paper_month = int(match.group(2)[2:4])

        now = datetime.now()
        current_year = now.year
        current_month = now.month
        current_day = now.day

        # 判断当前日期是否在本月或下月前5天
        if (current_year == paper_year and current_month == paper_month) or \
        (current_year == paper_year and current_month == paper_month + 1 and current_day <= 5):
            if link_type == "abs":
                return arxiv_url.replace("abs", "html")
            else:
                return arxiv_url  # 如果已经是 html 格式则不变
        else:
            if "ar5iv" in arxiv_url:
                return arxiv_url
            else:
                return arxiv_url.replace("arxiv", "ar5iv")

    def run(self):
        try:
            self.check_cancelled()
            self.signals.progress.emit(self.row, 1)  # Stage 1
            arxiv_url = self.check_arxiv_date_and_modify_url(self.url)
            if not arxiv_url:
                raise Exception("不合法的 Arxiv 路径")

            self.check_cancelled()
            self.signals.progress.emit(self.row, 2)  # Stage 2
            with sync_playwright() as p:
                # Launch browser and load extension if needed
                if not os.path.exists(resource_path(self.args['user_data_dir'])):
                    raise Exception(f"无法找到用户数据目录: {resource_path(self.args['user_data_dir'])}")

                if not os.path.exists(resource_path(self.args['extension_path'])):
                    raise Exception(f"无法找到扩展目录: {resource_path(self.args['extension_path'])}")
                
                if not os.path.exists(self.args['zotero_storage']):
                    raise Exception(f"无法找到 Zotero 存储目录: {self.args['zotero_storage']}")

                browser_context = p.chromium.launch_persistent_context(
                    user_data_dir=resource_path(self.args['user_data_dir']),
                    headless=False,
                    args=[
                        "--headless=new",
                        f'--disable-extensions-except={resource_path(self.args["extension_path"])}',
                        f'--load-extension={resource_path(self.args["extension_path"])}',
                    ],
                )

                self.check_cancelled()
                self.signals.progress.emit(self.row, 3)  # Stage 3
                page = browser_context.new_page()
                page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()

                # Navigate to URL
                page.goto(arxiv_url, wait_until='networkidle')

                page_title = page.title()
                page_title = re.sub(r'\[.*\]', '', page_title).strip()
                
                self.check_cancelled()
                self.signals.title.emit(self.row, page_title)

                self.check_cancelled()
                self.signals.progress.emit(self.row, 4)  # Stage 4
                # Wait for translation (adjust selector as needed)
                try:
                    page.wait_for_selector('font.immersive-translate-loading-spinner.notranslate', state='detached', timeout=1200000)
                except Exception:
                    raise Exception("等待翻译完成超时，可能翻译尚未完成")

                self.check_cancelled()
                self.signals.progress.emit(self.row, 5)  # Stage 5
                html_content = page.content()
                output_filename = re.sub(r'\[.*?\]', '', page.title()).strip() + ".html"
                output_filepath = os.path.join(self.args['output_dir'], output_filename)

                soup = BeautifulSoup(html_content, 'html.parser')

                resource_tags = []
                resource_tags.extend(soup.find_all('img', src=True))
                resource_tags.extend(soup.find_all('link', href=True, rel='stylesheet'))
                resource_tags.extend(soup.find_all('script', src=True))

                resource_map = {}
                base_url = page.url

                resources = []
                for tag in resource_tags:
                    if tag.name == 'img':
                        url_attr = 'src'
                        media_type = 'image'
                    elif tag.name == 'link':
                        url_attr = 'href'
                        media_type = 'text/css'
                    elif tag.name == 'script':
                        url_attr = 'src'
                        media_type = 'application/javascript'
                    else:
                        continue

                    resource_url = tag.get(url_attr)
                    if resource_url and not resource_url.startswith('data:'):
                        resource_url_absolute = urljoin(base_url, resource_url)
                        resources.append({
                            'tag': tag,
                            'url_attr': url_attr,
                            'resource_url': resource_url,
                            'resource_url_absolute': resource_url_absolute,
                            'media_type': media_type
                        })

                def download_and_encode(resource):
                    resource_url_absolute = resource['resource_url_absolute']
                    media_type = resource['media_type']
                    resource_url = resource['resource_url']

                    try:
                        response = requests.get(resource_url_absolute, timeout=10)
                        response.raise_for_status()
                    except Exception as e:
                        raise Exception(f"下载资源失败 {resource_url_absolute} ")

                    content_type = response.headers.get('Content-Type')
                    if not content_type:
                        content_type, _ = mimetypes.guess_type(resource_url_absolute)

                    if not content_type:
                        content_type = media_type

                    data_base64 = base64.b64encode(response.content).decode('utf-8')
                    data_url = f'data:{content_type};base64,{data_base64}'

                    resource_map[resource_url] = data_url

                self.check_cancelled()
                self.signals.progress.emit(self.row, 6)  # Stage 6
                with ThreadPoolExecutor(max_workers=32) as executor:
                    list(tqdm(executor.map(download_and_encode, resources), total=len(resources), desc="Downloading resources"))

                for resource in resources:
                    tag = resource['tag']
                    url_attr = resource['url_attr']
                    resource_url = resource['resource_url']
                    data_url = resource_map.get(resource_url)
                    if data_url:
                        tag[url_attr] = data_url

                if not os.path.exists(self.args['output_dir']):
                    os.makedirs(self.args['output_dir'])

                with open(output_filepath, 'w', encoding='utf-8') as f:
                    f.write(str(soup))

                self.check_cancelled()
                self.signals.progress.emit(self.row, 7)  # Stage 7
                # Save to Zotero
                zot = zotero.Zotero(
                    self.args['library_id'],
                    self.args['library_type'],
                    self.args['api_key']
                )

                try:
                    item = zot.item_template('webpage')
                    # 去除 [] 中的内容
                    item['title'] = page_title
                    item['url'] = page.url

                    if self.args['collection_key']:
                        item['collections'] = [self.args['collection_key']]
                    item = zot.create_items([item])
                    item_key = list(item['successful'].values())[0]['key']

                    storage_path = os.path.join(self.args['zotero_storage'], item_key)
                    if not os.path.exists(storage_path):
                        os.makedirs(storage_path)

                    attachment_path = os.path.join(storage_path, output_filename)
                    shutil.copy(output_filepath, attachment_path)

                    attachment = {
                        'itemType': 'attachment',
                        'parentItem': item_key,
                        'linkMode': 'linked_file',
                        'accessDate': datetime.now().strftime('%Y-%m-%d'),
                        'title': 'Snapshot',
                        'path': attachment_path,
                        'contentType': 'text/html'
                    }

                    response = zot.create_items([attachment])

                    if 'successful' in response and response['successful']:
                        pass
                    else:
                        raise Exception("创建附件失败")

                except Exception as e:
                    raise Exception(f"保存失败，错误信息: {e}")
           
                self.check_cancelled()
                browser_context.close()

            self.check_cancelled()
            self.signals.finished.emit(self.row, output_filepath)

        except Exception as e:
            if str(e) == "Cancel Task":
                pass
            else:
                self.signals.error.emit(self.row, str(e))
        finally:
            if 'browser_context' in locals():
                browser_context.close()
    


class CollectionDialog(QDialog):
    def __init__(self, collections, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择文献库")
        self.setMinimumSize(300, 400)

        layout = QVBoxLayout(self)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree)

        self.populate_tree(collections)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)

        self.setLayout(layout)

    def populate_tree(self, collections, parent=None):
        for collection in collections:
            item = QTreeWidgetItem(parent or self.tree)
            item.setText(0, collection['name'])
            item.setData(0, Qt.UserRole, collection['key'])
            if collection['children']:
                self.populate_tree(collection['children'], item)
            if not parent:
                item.setExpanded(True)

    def get_selected_collection(self):
        selected_items = self.tree.selectedItems()
        if selected_items:
            item = selected_items[0]
            return item.data(0, Qt.UserRole), item.text(0)
        return None, None

# --- Configuration Dialog ---
class ConfigDialog(QDialog):
    def __init__(self, current_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置配置")
        self.setModal(True)
        self.resize(750, 300)
        self.current_config = current_config

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.form_layout = QFormLayout()

        # Library ID
        self.library_id_input = QLineEdit(self.current_config.get('library_id', ''))
        self.library_id_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.library_id_input.setMinimumWidth(450)  # 增加宽度
        self.form_layout.addRow("Library ID:", self.library_id_input)

        # API Key
        self.api_key_input = QLineEdit(self.current_config.get('api_key', ''))
        self.api_key_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.api_key_input.setMinimumWidth(450)  # 增加宽度
        self.form_layout.addRow("API Key:", self.api_key_input)

        # Library Type
        self.library_type_input = QLineEdit(self.current_config.get('library_type', 'user'))
        self.library_type_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.library_type_input.setMinimumWidth(450)  # 增加宽度
        self.form_layout.addRow("Library Type:", self.library_type_input)

        # Zotero Storage Directory
        self.zotero_storage_input = QLineEdit(self.current_config.get('zotero_storage', ''))
        self.zotero_storage_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.zotero_storage_input.setMinimumWidth(450)
        self.browse_zotero_storage_button = QPushButton("浏览")
        self.browse_zotero_storage_button.clicked.connect(self.browse_zotero_storage)
        self.browse_zotero_storage_button.setDefault(False)
        zotero_storage_layout = QHBoxLayout()
        zotero_storage_layout.addWidget(self.zotero_storage_input)
        zotero_storage_layout.addWidget(self.browse_zotero_storage_button)
        self.form_layout.addRow("Zotero Storage Directory:", zotero_storage_layout)

        # User Data Directory
        self.user_data_dir_input = QLineEdit(self.current_config.get('user_data_dir', ''))
        self.user_data_dir_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.user_data_dir_input.setMinimumWidth(450)  # 增加宽度
        self.browse_user_data_dir_button = QPushButton("浏览")
        self.browse_user_data_dir_button.clicked.connect(self.browse_user_data_dir)
        self.browse_user_data_dir_button.setDefault(False)
        user_data_dir_layout = QHBoxLayout()
        user_data_dir_layout.addWidget(self.user_data_dir_input)
        user_data_dir_layout.addWidget(self.browse_user_data_dir_button)
        self.form_layout.addRow("User Data Dir:", user_data_dir_layout)

        # Extension Path
        self.extension_path_input = QLineEdit(self.current_config.get('extension_path', ''))
        self.extension_path_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.extension_path_input.setMinimumWidth(450)  # 增加宽度
        self.browse_extension_path_button = QPushButton("浏览")
        self.browse_extension_path_button.clicked.connect(self.browse_extension_path)
        self.browse_extension_path_button.setDefault(False)
        extension_path_layout = QHBoxLayout()
        extension_path_layout.addWidget(self.extension_path_input)
        extension_path_layout.addWidget(self.browse_extension_path_button)
        self.form_layout.addRow("Extension Path:", extension_path_layout)

        # Output Directory
        self.output_dir_input = QLineEdit(self.current_config.get('output_dir', 'saved_pages'))
        self.output_dir_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.output_dir_input.setMinimumWidth(450)  # 增加宽度
        self.browse_output_dir_button = QPushButton("浏览")
        self.browse_output_dir_button.clicked.connect(self.browse_output_dir)
        self.browse_output_dir_button.setDefault(False)
        output_dir_layout = QHBoxLayout()
        output_dir_layout.addWidget(self.output_dir_input)
        output_dir_layout.addWidget(self.browse_output_dir_button)
        self.form_layout.addRow("Output Directory:", output_dir_layout)

        self.layout.addLayout(self.form_layout)

        # Spacer
        self.layout.addItem(QSpacerItem(40, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.save_config)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

    def browse_user_data_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 User Data Directory", "")
        if directory:
            self.user_data_dir_input.setText(directory)

    def browse_extension_path(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 Extension Path", "")
        if directory:
            self.extension_path_input.setText(directory)

    def browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 Output Directory", "")
        if directory:
            self.output_dir_input.setText(directory)

    def browse_zotero_storage(self):
        directory = QFileDialog.getExistingDirectory(self, "选择 Zotero Storage Directory", "")
        if directory:
            self.zotero_storage_input.setText(directory)

    def save_config(self):
        new_config = {
            "library_id": self.library_id_input.text().strip(),
            "library_type": self.library_type_input.text().strip(),
            "api_key": self.api_key_input.text().strip(),
            "user_data_dir": self.user_data_dir_input.text().strip(),
            "extension_path": self.extension_path_input.text().strip(),
            "output_dir": self.output_dir_input.text().strip(),
            "zotero_storage": self.zotero_storage_input.text().strip()
        }

        # Validate required fields
        required_fields = ["library_id", "library_type", "api_key", "user_data_dir", "extension_path", "output_dir"]
        for field in required_fields:
            if not new_config[field]:
                QMessageBox.warning(self, "缺少字段", f"{field} 不能为空。")
                return

        if not os.path.exists(new_config['zotero_storage']):
            QMessageBox.critical(self, "配置错误", f"无法找到 Zotero 存储目录: {new_config['zotero_storage']}")
            return
            
        if not os.path.exists(new_config['user_data_dir']):
            QMessageBox.critical(self, "配置错误", f"无法找到用户数据目录: {resource_path(new_config['user_data_dir'])}")
            return 
        
        if not os.path.exists(new_config['extension_path']):
            QMessageBox.critical(self, "配置错误", f"无法找到扩展目录: {resource_path(new_config['extension_path'])}")
            return
        
        if not os.path.exists(new_config['output_dir']):
            os.makedirs(new_config['output_dir'])

                # 如果 library_id 或 API 不可以登录
        try:
            zot = zotero.Zotero(
                new_config['library_id'],
                new_config['library_type'],
                new_config['api_key']
            )
            zot.collections()
        except Exception as e:
            QMessageBox.critical(self, "验证失败", f"无法验证您的 Zotero: 请检查您的 Zotero ID 和 API Key，并检查您的网络连接")
            return
            
    # Save to config.json
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(new_config, f, indent=4)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存配置: {e}")

# --- Hierarchical ComboBox for Collections ---
class TreeWidgetPopup(QDialog):
    """ TreeWidget for selecting collections """
    def __init__(self, collections, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择文件库')
        self.setModal(True)
        self.resize(300, 450)

        self.layout = QVBoxLayout()
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)

        self.layout.addWidget(self.tree_widget)
        self.setLayout(self.layout)

        self.build_tree(collections)

    def build_tree(self, collections, parent_item=None):
        for collection in collections:
            item = QTreeWidgetItem([collection['name']])
            item.setData(0, Qt.UserRole, collection['key'])
            if parent_item is None:
                self.tree_widget.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if 'children' in collection and collection['children']:
                self.build_tree(collection['children'], item)

    def get_selected_key(self):
        item = self.tree_widget.currentItem()
        if item:
            return item.data(0, Qt.UserRole)
        return None

class CollectionTreeView(QTreeView):
    collectionSelected = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setExpandsOnDoubleClick(False)
        self.clicked.connect(self.item_clicked)
        self.model = QStandardItemModel()
        self.setModel(self.model)
        self.setItemDelegate(NoFocusDelegate())

    def set_collections(self, collections):
        self.model.clear()
        self.add_collections(self.model.invisibleRootItem(), collections)

    def add_collections(self, parent_item, collections):
        for collection in collections:
            item = QStandardItem(collection['name'])
            item.setData(collection['key'], Qt.UserRole)
            parent_item.appendRow(item)
            if 'children' in collection and collection['children']:
                self.add_collections(item, collection['children'])

    def item_clicked(self, index):
        item = self.model.itemFromIndex(index)
        if item.hasChildren():
            if self.isExpanded(index):
                self.collapse(index)
            else:
                self.expand(index)
        else:
            key = item.data(Qt.UserRole)
            name = item.text()
            self.collectionSelected.emit(key, name)

class NoFocusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if option.state & QStyle.State_HasFocus:
            option.state = option.state ^ QStyle.State_HasFocus
        super().paint(painter, option, index)

class UrlItemWidget(QWidget):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self.url_label = QLabel(url)
        self.collection_button = QPushButton("选择文献库")
        layout.addWidget(self.url_label)
        layout.addWidget(self.collection_button)
        layout.setContentsMargins(0, 0, 0, 0)

class GlobalEventFilter(QObject):
    foreground_hotkey_pressed = pyqtSignal()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space and event.modifiers() == Qt.AltModifier:
                self.foreground_hotkey_pressed.emit()
                return True
        return super().eventFilter(obj, event)

class HotkeyListener(QObject):
    background_hotkey_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.listener = None

    def start_listening(self):
        self.listener = keyboard.GlobalHotKeys({
            '<alt>+<space>': self._on_hotkey
        })
        self.listener.start()

    def stop_listening(self):
        if self.listener:
            self.listener.stop()

    def _on_hotkey(self):
        self.background_hotkey_pressed.emit()

class URLDelegate(QItemDelegate):
    def paint(self, painter, option, index):
        if index.column() == 0:  # Assuming URL is in the first column
            text = index.data()
            width = option.rect.width()
            metrics = painter.fontMetrics()
            
            # Determine how many characters can fit
            ellipsis = '...'
            available_width = width - metrics.width(ellipsis) - 5  # 5 pixels for padding
            
            if metrics.width(text) > available_width:
                # If text is too long, show only the end part
                visible_text = text
                while metrics.width(visible_text) > available_width and len(visible_text) > 0:
                    visible_text = visible_text[1:]
                text = ellipsis + visible_text
            
            # Draw the text
            painter.drawText(option.rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        else:
            super().paint(painter, option, index)
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Webpage to Zotero Saver")
        self.is_handling_hotkey = False
        self.setStyleSheet("""
            QWidget {
                font-size: 12px;
            }
            QLineEdit, QPushButton, QTreeView {
                padding: 5px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QTableWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 4px;
                border: 1px solid #ccc;
            }
        """)

        self.layout = QVBoxLayout()
        self.layout.setSpacing(10)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(self.layout)

        # 创建 URL 输入和相关按钮的布局
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("输入URL...")
        self.url_input.returnPressed.connect(self.add_url)
        url_layout.addWidget(self.url_input, 3)

        self.selected_collection_input = QLineEdit()
        self.selected_collection_input.setReadOnly(True)
        self.selected_collection_input.setPlaceholderText("未选中")
        self.selected_collection_input.setMaximumWidth(80)
        url_layout.addWidget(self.selected_collection_input)

        self.select_collection_button = QPushButton("选择")
        self.select_collection_button.clicked.connect(self.show_collection_dialog)
        url_layout.addWidget(self.select_collection_button)

        self.add_url_button = QPushButton(" ↵ ")
        self.add_url_button.clicked.connect(self.add_url)
        url_layout.addWidget(self.add_url_button)

        self.toggle_button = QPushButton(" ▲ ")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self.toggle_lower_section)
        url_layout.addWidget(self.toggle_button)

        self.layout.addLayout(url_layout)

        # 创建一个容器 QWidget 来包裹表格和控制按钮
        self.lower_container = QWidget()
        self.lower_layout = QVBoxLayout()
        self.lower_container.setLayout(self.lower_layout)

        # 创建 URL Table
        self.table_widget = QTableWidget(0, 4)
        self.table_widget.setHorizontalHeaderLabels(
            [   
                "URL".center(10),        
                "文献库".center(10),
                "标题/信息".center(45), 
                "进度".center(45)        
            ])

        self.table_widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_widget.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lower_layout.addWidget(self.table_widget)

        url_delegate = URLDelegate(self.table_widget)
        self.table_widget.setItemDelegateForColumn(0, url_delegate)

        # Control Buttons
        self.control_layout = QHBoxLayout()
        self.control_layout.setSpacing(10)
        self.start_button = QPushButton("开始保存 (Ctrl/Command+Enter)")
        self.start_button.setShortcut("Ctrl+Return")
        self.start_button.clicked.connect(self.start_saving)
        self.clear_button = QPushButton("清除所有 (Ctrl/Command+Backspace)")
        self.clear_button.setShortcut("Ctrl+Backspace")
        self.clear_button.clicked.connect(self.clear_all)
        self.config_button = QPushButton("设置配置 (Ctrl/Command+,)")
        self.config_button.setShortcut("Ctrl+,")
        self.config_button.clicked.connect(self.set_config)
        self.control_layout.addWidget(self.start_button)
        self.control_layout.addWidget(self.clear_button)
        self.control_layout.addWidget(self.config_button)
        self.lower_layout.addLayout(self.control_layout)

        self.layout.addWidget(self.lower_container)

        # Initialize configuration
        self.args = self.load_config()
        

        # Thread Pool Executor
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.tasks = []
        self.row_event = {}  # 用于跟踪每行的任务

        # Load Zotero collections
        self.load_zotero_collections()

        # Current selected collection
        # 加载上次使用的文献库信息
        self.current_collection_key = self.args.get('last_used_collection_key', '')
        self.current_collection_name = self.args.get('last_used_collection_name', '')
        self.update_collection_display()

        if not self.check_accessibility_permissions():
            reply = QMessageBox.question(
                self,
                "权限不足",
                "程序需要辅助功能权限才能监听全局快捷键。\n"
                "是否前往系统偏好设置授予权限？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                subprocess.call(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

        # Setup System Tray
        self.setup_tray_icon()

        # Setup Global Hotkey Listener
        self.setup_global_hotkey()
        self.adjustSize()

    def __del__(self):
        if hasattr(self, 'hotkey_listener'):
            self.hotkey_listener.stop_listening()
        if hasattr(self, 'hotkey_thread'):
            self.hotkey_thread.quit()
            self.hotkey_thread.wait()
    
    def reset_handling_flag(self):
        self.is_handling_hotkey = False

    def handle_foreground_hotkey(self):
        if not self.is_handling_hotkey and self.isActiveWindow():
            self.is_handling_hotkey = True
            self.minimize_window()
            QTimer.singleShot(100, self.reset_handling_flag)

    def handle_background_hotkey(self):
        if not self.is_handling_hotkey and not self.isActiveWindow():
            self.is_handling_hotkey = True
            self.restore_window()
            QTimer.singleShot(100, self.reset_handling_flag)

    def minimize_window(self):
        self.hide()
        QApplication.processEvents()  # 确保事件循环及时处理
        # 发送系统级事件以将焦点从当前应用移除
        QApp = QApplication.instance()
        QApp.setActiveWindow(None)

    def restore_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.minimize_window()

    
    def toggle_lower_section(self):
        if self.toggle_button.isChecked():
            self.lower_container.hide()
            self.toggle_button.setText(" ▼ ")
        else:
            self.lower_container.show()
            self.toggle_button.setText(" ▲ ")
        self.adjustSize()  


    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("config/icon.png"))
        self.tray_icon.setToolTip("Webpage to Zotero Saver")
        tray_menu = QMenu()

        show_action = QAction("显示", self)
        show_action.triggered.connect(self.restore_window)
        tray_menu.addAction(show_action)

        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.minimize_window)
        tray_menu.addAction(hide_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()


    def setup_global_hotkey(self):
        
        self.global_filter = GlobalEventFilter()
        QApplication.instance().installEventFilter(self.global_filter)
        self.global_filter.foreground_hotkey_pressed.connect(self.handle_foreground_hotkey)

        self.hotkey_listener = HotkeyListener()
        self.hotkey_listener.background_hotkey_pressed.connect(self.handle_background_hotkey)
        
        self.hotkey_thread = QThread()
        self.hotkey_listener.moveToThread(self.hotkey_thread)
        self.hotkey_thread.started.connect(self.hotkey_listener.start_listening)
        self.hotkey_thread.start()


    def check_accessibility_permissions(self):
        """
        检查当前应用是否已被授予辅助功能权限。
        通过尝试执行一个需要权限的命令来判断。
        """
        try:
            # 在 macOS 上，可以使用 AppleScript 检查权限
            script = '''
            tell application "System Events"
                set isEnabled to UI elements enabled
            end tell
            return isEnabled
            '''
            result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
            return result.stdout.strip() == "true"
        except Exception as e:
            print(f"权限检查失败: {e}")
            return False

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_window()

    def toggle_window(self):
        if self.isVisible() and self.isActiveWindow():
            self.minimize_window()
        else:
            self.restore_window()

    def open_saved_html(self):
        row = self.table_widget.currentRow()
        if row >= 0:
            output_filename = self.table_widget.item(row, 2).text() + ".html"
            output_filepath = os.path.join(self.args['output_dir'], output_filename)
            if os.path.exists(output_filepath):
                os.system(f"open \"{output_filepath}\"")

    def load_zotero_collections(self):
        try:
            zot = zotero.Zotero(
                self.args['library_id'],
                self.args['library_type'],
                self.args['api_key']
            )
            collections = zot.collections()
            self.collections = self.build_collection_tree(collections)
        except Exception as e:
            self.set_config()

    def show_collection_dialog(self):
        if not hasattr(self, 'collections'):
            self.load_zotero_collections()
        
        dialog = CollectionDialog(self.collections, self)
        if dialog.exec_():
            selected_key, selected_name = dialog.get_selected_collection()
            if selected_key and selected_name:
                self.current_collection_key = selected_key
                self.current_collection_name = selected_name
                self.update_collection_display()
                self.save_current_collection()  # 保存当前选中的文献库

    def update_collection_display(self):
        if self.current_collection_name:
            # 只显示文献库名称的前10个字符
            display_name = self.current_collection_name[:10] + '...' if len(self.current_collection_name) > 10 else self.current_collection_name
            self.selected_collection_input.setText(display_name)
        else:
            self.selected_collection_input.clear()

    def save_current_collection(self):
        config = self.load_config()
        config['last_used_collection_key'] = self.current_collection_key
        config['last_used_collection_name'] = self.current_collection_name
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)

    def update_collection_display(self):
        if self.current_collection_name:
            self.selected_collection_input.setText(self.current_collection_name)
        else:
            self.selected_collection_input.clear()

    def on_collection_selected(self, key, name):
        self.current_collection_key = key
        self.current_collection_name = name

    def add_url(self):
        url = self.url_input.text().strip()
        if url:
            if not self.current_collection_key:
                QMessageBox.warning(self, "未选择文献库", "请先选择一个文献库。")
                return

            row_position = self.table_widget.rowCount()
            self.table_widget.insertRow(row_position)

            # URL Item
            url_item = QTableWidgetItem(url)
            url_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            url_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            self.table_widget.setItem(row_position, 0, url_item)

            # Collection Item
            collection_item = QTableWidgetItem(self.current_collection_name)
            collection_item.setData(Qt.UserRole, self.current_collection_key)
            collection_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            collection_item.setTextAlignment(Qt.AlignCenter)
            self.table_widget.setItem(row_position, 1, collection_item)

            # Title Item (initially empty)
            title_item = QTableWidgetItem("等待中")
            title_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            title_item.setTextAlignment(Qt.AlignCenter)
            self.table_widget.setItem(row_position, 2, title_item)

            # Progress Bar
            progress_bar = QProgressBar()
            progress_bar.setMaximum(7)
            progress_bar.setTextVisible(True)
            progress_bar.setFormat("等待开始")
            progress_bar.setAlignment(Qt.AlignCenter)
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #2196F3; }")
            self.table_widget.setCellWidget(row_position, 3, progress_bar)
            self.url_input.clear()

            self.start_saving()

    def start_saving(self):
        if self.table_widget.rowCount() == 0:
            QMessageBox.warning(self, "没有 URL", "请添加至少一个 URL 以保存。")
            return

        for row in range(self.table_widget.rowCount()):
            if row in self.row_event:
                continue  # 跳过已经在执行的任务

            url = self.table_widget.item(row, 0).text()
            collection_key = self.table_widget.item(row, 1).data(Qt.UserRole)

            progress_bar = self.table_widget.cellWidget(row, 3)

            # Reset progress bar
            progress_bar.setValue(0)
            progress_bar.setFormat("等待开始")

            # Create signals
            signals = WorkerSignals()
            signals.progress.connect(self.update_progress)
            signals.title.connect(self.update_title)
            signals.finished.connect(self.mark_finished)
            signals.error.connect(self.handle_error)

            cancel_event = threading.Event()

            # Create and submit worker
            worker = SavePageWorker(row, url, {**self.args, 'collection_key': collection_key}, signals, cancel_event)
            self.executor.submit(worker.run)
            self.row_event[row] = cancel_event



    def build_collection_tree(self, collections):
        tree = []
        lookup = {}
        deleted_keys = set()

        # 第一遍遍历：创建lookup字典和删除标记
        for collection in collections:
            key = collection['key']
            is_deleted = collection['data'].get('deleted', False)
            
            if is_deleted:
                deleted_keys.add(key)
            else:
                item = {
                    'name': collection['data']['name'],
                    'key': key,
                    'parentKey': collection['data'].get('parentCollection'),
                    'children': []
                }
                lookup[key] = item

        # 辅助函数：检查节点及其所有祖先是否有被删除的
        def is_ancestor_deleted(key):
            current = lookup.get(key)
            while current:
                if current['key'] in deleted_keys or current['parentKey'] in deleted_keys:
                    return True
                current = lookup.get(current['parentKey'])
            return False

        # 第二遍遍历：构建树结构，同时检查祖先节点
        for key in list(lookup.keys()):
            if is_ancestor_deleted(key):
                del lookup[key]
            else:
                item = lookup[key]
                if item['parentKey'] is None or item['parentKey'] not in lookup:
                    tree.append(item)
                else:
                    parent = lookup[item['parentKey']]
                    parent['children'].append(item)

        return tree
        
    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            # 创建默认配置
            default_config = {
                "zotero_storage": "在 Zotero 设置 -> 高级 -> 数据存储位置 获得地址，在后方加上 /storage",
                "library_id": "访问这里以获得ID https://www.zotero.org/settings/security#applications",
                "api_key": "访问这里以创建API https://www.zotero.org/settings/security#applications",
                "library_type": "user",
                "user_data_dir": resource_path('config/user_data'),
                "extension_path": resource_path('config/extension'),
                "output_dir": "download",
                "last_used_collection_key": "",
                "last_used_collection_name": ""
            }

            # 保存默认配置
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            return default_config
        else:
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                return config

            except Exception as e:
                QMessageBox.critical(self, "配置错误", f"无法读取配置文件: {e}")
                sys.exit(1)

    def clear_all(self):
        for row in self.row_event.values():
            row.set()
            del row
        self.row_event.clear()
        self.table_widget.setRowCount(0)

    def delete_selected_row(self):
        current_row = self.table_widget.currentRow()
        if current_row != -1:
            if current_row in self.row_event:
                cancel_event = self.row_event[current_row]
                cancel_event.set()
                del self.row_event[current_row]
            self.table_widget.removeRow(current_row)
            self.update_row_numbers()
            self.update_row_task_indices()

    def update_row_task_indices(self):
        new_row_event = {}
        for old_row, cancel_event in self.row_event.items():
            new_row = self.find_new_row_index(old_row)
            if new_row is not None:
                new_row_event[new_row] = cancel_event
        self.row_event = new_row_event

    def find_new_row_index(self, old_row):
        for row in range(self.table_widget.rowCount()):
            if self.table_widget.item(row, 0).text() == str(old_row + 1):
                return row
        return None

    def update_row_numbers(self):
        for row in range(self.table_widget.rowCount()):
            row_number_item = QTableWidgetItem(str(row + 1))
            row_number_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

    def set_config(self):
        dialog = ConfigDialog(self.args, self)
        if dialog.exec_() == QDialog.Accepted:
            self.args = self.load_config()
            self.load_zotero_collections()

    def update_progress(self, row, progress_value):
        progress_bar = self.table_widget.cellWidget(row, 3)
        progress_bar.setValue(progress_value)
        if 1 <= progress_value <= 7:
            stage = [
                "(1/7) 转换 Arxiv url",
                "(2/7) 启动浏览器并加载扩展",
                "(3/7) 访问目标网页",
                "(4/7) 等待页面内容翻译完成",
                "(5/7) 页面内容已加载并解析",
                "(6/7) 下载并编码资源",
                "(7/7) 保存到 Zotero"
            ][progress_value - 1]
            progress_bar.setFormat(stage)

    def update_title(self, row, title):
        title_item = self.table_widget.item(row, 2)
        title_item.setText(title)

    def mark_finished(self, row, filepath):
        progress_bar = self.table_widget.cellWidget(row, 3)
        progress_bar.setValue(7)
        progress_bar.setFormat("完成")
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #4CAF50; }")

    def handle_error(self, row, error_message):
        progress_bar = self.table_widget.cellWidget(row, 3)
        progress_bar.setFormat("错误")
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        # 显示错误信息在标题列
        title_item = self.table_widget.item(row, 2)
        title_item.setText(f"错误: {error_message}")
        print(f"Row {row} Error: {error_message}")


# --- Main Entry Point ---
def main():
    app = QApplication(sys.argv)
    
    if sys.platform == 'darwin':
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    
    app.setQuitOnLastWindowClosed(False) 
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()