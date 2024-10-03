# 简介
🚀 批量把 Arxiv 文献翻译为双语版本并保存到您的 Zotero 🚀

## 安装
### 先决条件
1. 安装 [Zotero](https://www.zotero.org/download/) 

### 安装依赖
```
pip install setuptools
pip install -e .
playwright install chromium
```

## 运行
```
python run.py
```
随后可以使用 `Option / Alt + Space` 快捷键 打开/关闭 插件。

## 首次运行时配置
在弹出的窗口中配置如下信息：
1. Zotero 数据库路径: 
   - Linux/MacOS 一般为 `~/Zotero/storage`
   - Windows 一般为 `C:\Users\用户名\Zotero\storage`
   请访问 Zotero 设置 -> 高级 -> 数据存储位置 获得地址，在此地址下会有一个 `storage` 文件夹，将其路径粘贴到配置中的 "Zotero Storage Path"。
2. Zotero Library ID: 
   
   请访问 [Zotero Applications](https://www.zotero.org/settings/security#applications)，获取 "Your user ID for use in API calls is " 后方的数字并粘贴至配置中的 "Library ID"。
3. Zotero API Key: 
   
   请继续访问 [Zotero Applications](https://www.zotero.org/settings/security#applications):
    1. 点击 "Create new private key"
    2. 随意填写 "Key Name"，如 "AutoSaveToZotero"
    3. 将 "Default Group Permissions" 设为 "Read/Write"
    4. 点击 "Save Key"，获得生成的 API Key 并粘贴至配置中的 "API Key"。

## 高级配置（可选）
1. 更换翻译 API 和其他翻译设定:
    
    默认使用的是 智谱GLM翻译API，如需更换翻译API，请在 Chrome 浏览器中对沉浸式翻译的配置进行更改，随后请将插件的配置文件覆盖 `config/extension` 文件夹。
    
    具体而言，分为如下步骤：
    1.  在 Chrome 的沉浸式翻译插件中，配置翻译API为您所需的API，并修改其它设定。为方便使用，请确保`翻译设置 -> 进阶设置 -> 进入网页后，是否立即翻译到页面底部` 设置为开启（您可以在导出配置文件后关闭该选项）
    2. 在地址栏输入 `chrome://extensions/`，右上角打开开发者模式，找到沉浸式翻译插件，记录其 ID（形为 "bpoadfkcbjbfhfodi****hhhpibjhbnh", * 仅用于隐匿个人 ID 使用 ）
    3. 在地址栏输入 `chrome://version/`，获取`个人资料路径`并打开该路径。
    4. 复制 `Default` 文件夹至 `config/user_data` 中覆盖原有文件夹。
   
