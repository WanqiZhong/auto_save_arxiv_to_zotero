from setuptools import setup, find_packages

setup(
    name='AutoSaveToZotero',
    version='1.0.0',
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