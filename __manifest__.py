{
    'name': 'China Cloud OCR for Expense (百度/腾讯/自定义 OCR)',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Expenses',
    'summary': '使用国内在线 OCR 替换 Odoo 官方 IAP OCR，支持百度 AI、腾讯云及自定义 OCR 接口',
    'description': """
Odoo 19 Expense 国内在线 OCR 模块
====================================
* 支持百度智能云 OCR (增值税发票识别、通用票据识别)
* 支持腾讯云 OCR (增值税发票识别)
* 支持自定义 RESTful OCR 接口
* 无需购买 Odoo IAP 积分，零扣费
* 界面菜单支持灵活配置 API Key、Secret Key 与 Endpoint URL
    """,
    'author': 'Antigravity AI',
    'depends': ['hr_expense', 'hr_expense_extract'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
