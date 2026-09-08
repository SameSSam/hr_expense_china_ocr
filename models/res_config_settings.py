from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    china_ocr_provider = fields.Selection(
        selection=[
            ('baidu', '百度智能云 OCR (Baidu AI)'),
            ('tencent', '腾讯云 OCR (Tencent Cloud)'),
            ('custom', '自定义 REST OCR (Custom API)'),
        ],
        string="OCR 服务商",
        default='baidu',
        config_parameter='hr_expense_china_ocr.provider',
    )
    china_ocr_api_key = fields.Char(
        string="API Key / Client ID / SecretId",
        config_parameter='hr_expense_china_ocr.api_key',
        help="百度 AI 的 API Key (Client ID)，或腾讯云 SecretId"
    )
    china_ocr_secret_key = fields.Char(
        string="Secret Key / Client Secret",
        config_parameter='hr_expense_china_ocr.secret_key',
        help="百度 AI 的 Secret Key (Client Secret)，或腾讯云 SecretKey"
    )
    china_ocr_api_url = fields.Char(
        string="OCR 识别接口 URL",
        default='https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice',
        config_parameter='hr_expense_china_ocr.api_url',
        help="例如百度的增值税发票识别 URL: https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice"
    )
    china_ocr_token_url = fields.Char(
        string="OAuth Token URL (百度专享)",
        default='https://aip.baidubce.com/oauth/2.0/token',
        config_parameter='hr_expense_china_ocr.token_url',
        help="百度获取 Access Token 的 URL: https://aip.baidubce.com/oauth/2.0/token"
    )
