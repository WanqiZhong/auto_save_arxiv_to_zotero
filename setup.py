from setuptools import setup, find_packages
import os
import sys

def collect_data_files(src_dir, dest_dir):
    """
    递归地收集 src_dir 目录下的所有文件，将其包含在 dest_dir 目录下。
    """
    data_files = []
    for root, dirs, files in os.walk(src_dir):
        rel_path = os.path.relpath(root, os.path.dirname(src_dir))
        destination = os.path.join(dest_dir, rel_path)
        file_paths = [os.path.join(root, f) for f in files]
        if file_paths:
            data_files.append((destination, file_paths))
    return data_files

APP = ['run.py']

def is_py2app():
    """
    判断是否正在运行 py2app 命令。
    """
    return 'py2app' in sys.argv

if is_py2app():
    # 收集需要包含的数据文件
    DATA_FILES = collect_data_files('config', 'config')

    OPTIONS = {
        'argv_emulation': True,
        'packages': ['PyQt5', 'playwright', 'bs4', 'tqdm', 'pyzotero'],
        'iconfile': 'config/icon.png',  # 确保这个路径正确
        'plist': {
            'CFBundleName': 'AutoSaveToZotero',
            'CFBundleShortVersionString': '1.1.0',
            'CFBundleVersion': '1.1.0',
            'CFBundleIdentifier': 'com.wdaxiwan.AutoSaveToZotero',
            'NSHumanReadableCopyright': 'Copyright © 2024 Wdaxiwan',
            'NSHighResolutionCapable': True,
        },
        'includes': ['PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'playwright.sync_api']
    }

    setup(
        app=APP,
        name='AutoSaveToZotero',
        version='1.1.0',
        packages=find_packages(),
        data_files=DATA_FILES,
        options={'py2app': OPTIONS},
        setup_requires=['py2app'],
        install_requires=[
            'requests>=2.25.1',
            'PyQt5>=5.15.11',
            'playwright>=1.47.0',
            'beautifulsoup4>=4.11.2',
            'tqdm>=4.66.1',
            'pyzotero>=1.5.25'
        ],
    )
else:
    # 为 pip 安装配置
    setup(
        name='AutoSaveToZotero',
        version='1.1.0',
        packages=find_packages(),
        install_requires=[
            'requests>=2.25.1',
            'PyQt5>=5.15.11',
            'playwright>=1.47.0',
            'beautifulsoup4>=4.11.2',
            'tqdm>=4.66.1',
            'pyzotero>=1.5.25',
        ],
    )