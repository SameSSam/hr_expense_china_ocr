import base64
import json
import logging
import re
import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    date_from_subject = fields.Boolean(
        string="Date from subject",
        default=False,
        copy=False,
        help="Indicates whether the expense date was explicitly provided in the email subject."
    )

    def buy_credits(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("国内在线 OCR 已启用"),
                'message': _("系统已启用国内在线 OCR (百度/腾讯/自定义 API)，无需在 Odoo IAP 充值。"),
                'sticky': False,
                'type': 'info',
            }
        }

    @api.model
    def _parse_expense_date(self, expense_description):
        """Parse date from expense description (email subject).
        Supports:
        1. Bracket format: '[2024-12-05]', '[2024/12/05]', '[2024.12.05]'
        2. Chinese format: '2024年12月05日'
        3. Standalone standard date: '2024-12-05', '2024/12/05', '2024.12.05'
        Returns: (parsed_date_str_or_False, clean_description)
        """
        if not expense_description:
            return False, expense_description

        # 1. Match bracket format first, e.g. [2024-12-05], [2024/12/05], [2024.12.05]
        bracket_match = re.search(r'\[(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\]', expense_description)
        if bracket_match:
            full_str = bracket_match.group(0)
            raw_date = bracket_match.group(1)
            formatted_date = self._format_date_str(raw_date)
            if formatted_date:
                clean_description = expense_description.replace(full_str, ' ')
                clean_description = re.sub(r'\s+', ' ', clean_description).strip()
                return formatted_date, clean_description

        # 2. Match Chinese date format, e.g. 2024年12月05日
        cn_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日?)', expense_description)
        if cn_match:
            full_str = cn_match.group(0)
            formatted_date = self._format_date_str(full_str)
            if formatted_date:
                clean_description = expense_description.replace(full_str, ' ')
                clean_description = re.sub(r'\s+', ' ', clean_description).strip()
                return formatted_date, clean_description

        # 3. Match standalone date format, e.g. 2024-12-05, 2024/12/05, 2024.12.05
        std_match = re.search(r'(?:^|[\s,;])(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?:$|[\s,;])', expense_description)
        if std_match:
            raw_date = std_match.group(1)
            formatted_date = self._format_date_str(raw_date)
            if formatted_date:
                clean_description = expense_description.replace(raw_date, ' ')
                clean_description = re.sub(r'\s+', ' ', clean_description).strip()
                return formatted_date, clean_description

        return False, expense_description

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Override to extract expense date from email subject before standard parsing.
        If a date is found in subject:
        1. Inject 'date': parsed_date and 'date_from_subject': True into custom_values.
        2. Clean the date from msg_dict['subject'] so _parse_price does not mistake the year/date for amount.
        """
        if custom_values is None:
            custom_values = {}

        subject = msg_dict.get('subject', '')
        if subject:
            parsed_date, clean_subject = self._parse_expense_date(subject)
            if parsed_date:
                msg_dict['subject'] = clean_subject
                custom_values.setdefault('date', parsed_date)
                custom_values.setdefault('date_from_subject', True)

        return super().message_new(msg_dict, custom_values=custom_values)

    @api.model
    def _parse_product(self, expense_description):
        """Enhanced product parser:
        1. Supports bracket format anywhere in subject, e.g. '购买饮料、水果及糕点等食品 [FOOD_STF] 340.57'
        2. Matches product default_code (Internal Reference / 内部引用代码) or Name
        3. Removes [CODE] from description text and sets matched product category
        """
        if expense_description:
            match = re.search(r'\[(.*?)\]', expense_description)
            if match:
                full_bracket_str = match.group(0)  # e.g. '[FOOD_STF]'
                code = match.group(1).strip()       # e.g. 'FOOD_STF'

                product = self.env['product.product'].search([
                    ('can_be_expensed', '=', True),
                    '|',
                    ('default_code', '=ilike', code),
                    ('name', '=ilike', code)
                ], limit=1)

                if product:
                    clean_description = expense_description.replace(full_bracket_str, '').strip()
                    clean_description = re.sub(r'\s+', ' ', clean_description)
                    return product, clean_description

        return super()._parse_product(expense_description)

    def _fill_document_with_results(self, ocr_results):
        """Override to implement Smart Merge Strategy:
        If fields (name, price_unit, product_id) were already populated by email subject parsing
        or manual input, DO NOT overwrite them with OCR results. Only fill missing fields (like invoice date).
        """
        if not ocr_results or self.state != 'draft':
            return super()._fill_document_with_results(ocr_results)

        total_ocr = self._get_ocr_selected_value(ocr_results, 'total', 0.0)
        date_ocr = self._get_ocr_selected_value(ocr_results, 'date', False)
        desc_ocr = self._get_ocr_selected_value(ocr_results, 'description', '')

        user_id = self.employee_id.user_id if self.employee_id.user_id else self.env.uid
        default_receipt_name = self.with_user(user_id)._get_untitled_expense_name("").strip()

        vals = {}

        # 1. Description & Product: Only update if name is default/untitled and not specified by email subject
        if desc_ocr and (not self.name or default_receipt_name in self.name or self.name == '国内发票报销'):
            vals['name'] = desc_ocr
            if not self.product_id:
                predicted_product_id = self._predict_product(desc_ocr)
                if predicted_product_id:
                    vals['product_id'] = predicted_product_id

        # 2. Amount: Only update if price_unit/total_amount_currency was NOT specified in email subject (is 0 or unset)
        if total_ocr and (not self.price_unit or self.price_unit == 0.0 or not self.total_amount_currency):
            vals['price_unit'] = total_ocr
            vals['total_amount_currency'] = total_ocr

        # 3. Date: Fill invoice date from OCR only if date was NOT specified in email subject
        if date_ocr and not self.date_from_subject:
            vals['date'] = date_ocr

        if vals:
            self.write(vals)

    def _upload_to_extract(self):
        """Override to support domestic Cloud OCR (Baidu / Tencent / Custom API)."""
        get_param = self.env['ir.config_parameter'].sudo().get_param
        provider = get_param('hr_expense_china_ocr.provider', 'baidu')

        if not provider:
            return super()._upload_to_extract()

        for record in self:
            if not record.message_main_attachment_id:
                continue

            attachment = record.message_main_attachment_id
            if record.extract_state not in ['no_extract_requested', 'not_enough_credit', 'error_status']:
                continue

            try:
                ocr_results = record._call_china_ocr_api(attachment, provider)
                if ocr_results:
                    record._fill_document_with_results(ocr_results)
                    record.extract_state = 'done'
                    record.extract_status = 'success'
                    record.message_post(body=_("✅ 已通过国内 OCR (%s) 成功自动提取发票数据。") % provider.upper())
                else:
                    record.extract_state = 'error_status'
                    record.extract_status = 'error_no_connection'
                    record.message_post(body=_("⚠️ 国内 OCR (%s) 识别失败，未能提取到有效票据数据。") % provider.upper())
            except Exception as e:
                _logger.exception("China OCR recognition error: %s", str(e))
                record.extract_state = 'error_status'
                record.extract_status = 'error_internal'
                record.message_post(body=_("❌ OCR 识别异常: %s") % str(e))

    def _call_china_ocr_api(self, attachment, provider):
        """Route to specific OCR provider implementation."""
        get_param = self.env['ir.config_parameter'].sudo().get_param
        api_key = get_param('hr_expense_china_ocr.api_key', '')
        secret_key = get_param('hr_expense_china_ocr.secret_key', '')
        api_url = get_param('hr_expense_china_ocr.api_url', '')

        file_data = base64.b64decode(attachment.datas)

        if provider == 'baidu':
            token_url = get_param('hr_expense_china_ocr.token_url', 'https://aip.baidubce.com/oauth/2.0/token')
            return self._call_baidu_ocr(file_data, api_key, secret_key, api_url, token_url)
        elif provider == 'tencent':
            return self._call_tencent_ocr(file_data, api_key, secret_key, api_url)
        elif provider == 'custom':
            return self._call_custom_ocr(file_data, api_key, api_url)
        return None

    def _format_date_str(self, date_val):
        """Format raw date string into standard YYYY-MM-DD."""
        if not date_val:
            return False
        date_str = str(date_val).strip()
        date_clean = date_str.replace('年', '-').replace('月', '-').replace('日', '').strip()

        # Handle YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
        match = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', date_clean)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # Handle 8-digit continuous number: 20241205
        match_compact = re.search(r'\b(\d{4})(\d{2})(\d{2})\b', date_clean)
        if match_compact:
            year, month, day = match_compact.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return False

    def _call_baidu_ocr(self, file_bytes, api_key, secret_key, api_url, token_url):
        """Call Baidu AI OCR API and return Odoo formatted dict."""
        if not api_key or not secret_key:
            raise UserError(_("未配置百度的 API Key 或 Secret Key，请至【设置 -> 费用】中进行配置。"))

        if not api_url:
            api_url = 'https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice'

        # 1. Get Baidu Access Token
        token_params = {
            'grant_type': 'client_credentials',
            'client_id': api_key.strip(),
            'client_secret': secret_key.strip(),
        }
        res_token = requests.post(token_url, params=token_params, timeout=10)
        res_token_json = res_token.json()
        access_token = res_token_json.get('access_token')

        if not access_token:
            error_msg = res_token_json.get('error_description') or res_token_json.get('error') or '无法获取 Access Token'
            raise UserError(_("百度 OCR 鉴权失败: %s") % error_msg)

        # 2. Call OCR API
        request_url = f"{api_url}?access_token={access_token}"
        img_base64 = base64.b64encode(file_bytes).decode('utf-8').replace('\r', '').replace('\n', '')

        if file_bytes.startswith(b'%PDF'):
            payload = {'pdf_file': img_base64}
        else:
            payload = {'image': img_base64}

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        res_ocr = requests.post(request_url, data=payload, headers=headers, timeout=15)
        res_ocr_json = res_ocr.json()

        _logger.info("Baidu OCR raw response: %s", res_ocr_json)

        words_result = res_ocr_json.get('words_result', {})
        if not words_result:
            error_msg = res_ocr_json.get('error_msg', '未返回有效字段')
            _logger.warning("Baidu OCR response error: %s", error_msg)
            return None

        description = ''
        total_val = '0.0'
        date_val = ''

        if isinstance(words_result, dict):
            commodity_list = words_result.get('CommodityName', [])
            if isinstance(commodity_list, list) and commodity_list:
                description = commodity_list[0].get('word', '')
            if not description:
                seller = words_result.get('SellerName', {})
                description = seller.get('word', '') if isinstance(seller, dict) else str(seller or '')
            if not description:
                inv_type = words_result.get('InvoiceType', {})
                description = inv_type.get('word', '') if isinstance(inv_type, dict) else str(inv_type or '增值税发票报销')

            total_val = words_result.get('AmountInFig') or words_result.get('TotalAmount') or words_result.get('total_amount') or '0.0'
            if isinstance(total_val, dict):
                total_val = total_val.get('word', '0.0')

            date_val = words_result.get('InvoiceDate') or words_result.get('date') or ''
            if isinstance(date_val, dict):
                date_val = date_val.get('word', '')

        try:
            total_amount = float(str(total_val).replace('￥', '').replace('¥', '').replace('$', '').replace(',', '').strip())
        except ValueError:
            total_amount = 0.0

        date_str = self._format_date_str(date_val)

        return {
            'description': {'selected_value': {'content': description or '国内发票报销'}},
            'total': {'selected_value': {'content': total_amount}},
            'date': {'selected_value': {'content': date_str}},
            'currency': {'selected_value': {'content': 'CNY'}},
        }

    def _call_tencent_ocr(self, file_bytes, secret_id, secret_key, api_url):
        """Call Tencent Cloud OCR API via official tencentcloud SDK."""
        if not secret_id or not secret_key:
            raise UserError(_("未配置腾讯云 SecretId 或 SecretKey，请至【设置 -> 费用】中配置。"))

        img_base64 = base64.b64encode(file_bytes).decode('utf-8').replace('\r', '').replace('\n', '')
        is_pdf = file_bytes.startswith(b'%PDF')

        try:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.ocr.v20181119 import ocr_client, models

            cred = credential.Credential(secret_id.strip(), secret_key.strip())
            httpProfile = HttpProfile()
            httpProfile.endpoint = "ocr.tencentcloudapi.com"

            clientProfile = ClientProfile()
            clientProfile.httpProfile = httpProfile

            client = ocr_client.OcrClient(cred, "ap-guangzhou", clientProfile)

            req = models.VatInvoiceOCRRequest()
            req.ImageBase64 = img_base64
            if is_pdf:
                req.IsPdf = True
                req.PdfPageNumber = 1

            resp = client.VatInvoiceOCR(req)
            resp_dict = json.loads(resp.to_json_string())

            _logger.info("Tencent VatInvoiceOCR raw response: %s", resp_dict)

            infos = resp_dict.get('VatInvoiceInfos', [])
            info_dict = {item.get('Name'): item.get('Value') for item in infos if isinstance(item, dict)}

            # Extract item name or seller name
            items = resp_dict.get('Items', [])
            description = ''
            if items and isinstance(items, list) and isinstance(items[0], dict):
                description = items[0].get('Name', '')

            if not description:
                description = info_dict.get('销售方名称') or info_dict.get('SellerName') or info_dict.get('发票名称') or '增值税发票'

            # Extract total amount (check Chinese keys '价税合计(小写)' / '合计金额')
            total_val = (
                info_dict.get('价税合计(小写)')
                or info_dict.get('合计金额')
                or info_dict.get('Total')
                or info_dict.get('AmountInFig')
                or info_dict.get('TotalAmount')
                or '0.0'
            )

            try:
                total_amount = float(
                    str(total_val)
                    .replace('￥', '')
                    .replace('¥', '')
                    .replace('$', '')
                    .replace(',', '')
                    .strip()
                )
            except ValueError:
                total_amount = 0.0

            # Extract date (check Chinese key '开票日期')
            raw_date = info_dict.get('开票日期') or info_dict.get('IssueDate') or info_dict.get('Date') or ''
            date_str = self._format_date_str(raw_date)

            return {
                'description': {'selected_value': {'content': description}},
                'total': {'selected_value': {'content': total_amount}},
                'date': {'selected_value': {'content': date_str}},
                'currency': {'selected_value': {'content': 'CNY'}},
            }
        except Exception as e:
            _logger.warning("Tencent VatInvoiceOCR failed, trying SmartStructuralOCR: %s", str(e))
            try:
                from tencentcloud.common import credential
                from tencentcloud.ocr.v20181119 import ocr_client, models

                cred = credential.Credential(secret_id.strip(), secret_key.strip())
                client = ocr_client.OcrClient(cred, "ap-guangzhou")
                req = models.SmartStructuralOCRRequest()
                req.ImageBase64 = img_base64
                if is_pdf:
                    req.IsPdf = True
                    req.PdfPageNumber = 1

                resp = client.SmartStructuralOCR(req)
                resp_dict = json.loads(resp.to_json_string())

                struct_info = resp_dict.get('StructuralInformation', {})
                total_val = '0.0'
                seller_name = '通用发票报销'
                date_val = ''

                if isinstance(struct_info, dict):
                    items = struct_info.get('Item', [])
                    for item in items:
                        key = item.get('Key', '')
                        val = item.get('Value', '')
                        if '金额' in key or '合计' in key or 'Total' in key:
                            total_val = val
                        elif '日期' in key or 'Date' in key:
                            date_val = val
                        elif '名称' in key or '商户' in key:
                            seller_name = val

                try:
                    total_amount = float(
                        str(total_val)
                        .replace('￥', '')
                        .replace('¥', '')
                        .replace('$', '')
                        .replace(',', '')
                        .strip()
                    )
                except ValueError:
                    total_amount = 0.0

                date_str = self._format_date_str(date_val)

                return {
                    'description': {'selected_value': {'content': seller_name}},
                    'total': {'selected_value': {'content': total_amount}},
                    'date': {'selected_value': {'content': date_str}},
                    'currency': {'selected_value': {'content': 'CNY'}},
                }
            except Exception as e2:
                _logger.exception("Tencent SmartStructuralOCR failed: %s", str(e2))
                raise UserError(_("腾讯云 OCR 调用失败: %s") % str(e))

    def _call_custom_ocr(self, file_bytes, api_key, api_url):
        """Call Custom REST OCR API."""
        if not api_url:
            raise UserError(_("未配置自定义 OCR 接口 URL。"))

        headers = {'Authorization': f'Bearer {api_key.strip()}'} if api_key else {}
        img_base64 = base64.b64encode(file_bytes).decode('utf-8').replace('\r', '').replace('\n', '')
        payload = {'image_base64': img_base64}

        res = requests.post(api_url.strip(), json=payload, headers=headers, timeout=15)
        res_json = res.json()

        if 'description' in res_json or 'total' in res_json:
            return {
                'description': {'selected_value': {'content': res_json.get('description', '发票报销')}},
                'total': {'selected_value': {'content': float(res_json.get('total', 0.0))}},
                'date': {'selected_value': {'content': self._format_date_str(res_json.get('date', ''))}},
                'currency': {'selected_value': {'content': res_json.get('currency', 'CNY')}},
            }
        return res_json
