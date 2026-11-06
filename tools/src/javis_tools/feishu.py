"""Minimal bot delivery. Tokens and vendor response bodies never reach logs."""

import json
import re

import httpx

BASE = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    def __init__(self, category, *, uncertain=False, code=None):
        self.category, self.uncertain = category, uncertain
        self.code = code if type(code) is int else None
        super().__init__(category)


class FeishuBot:
    def __init__(self, app_id, app_secret, recipient, *, client=None):
        self.app_id, self._secret, self.recipient = app_id, app_secret, recipient
        self._token = None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False,
        )

    def authenticate(self):
        try:
            response = self.client.post(
                BASE + "/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self._secret},
            )
            value = response.json()
            if response.status_code != 200 or value.get("code") != 0:
                raise FeishuError("feishu_auth_rejected", code=value.get("code"))
            token = value.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise FeishuError("feishu_auth_invalid")
            self._token = token
        except FeishuError:
            raise
        except Exception:
            raise FeishuError("feishu_auth_unavailable") from None

    def send(self, content, delivery_id):
        if not self._token:
            raise FeishuError("feishu_not_authenticated")
        try:
            response = self.client.post(
                BASE + "/im/v1/messages",
                params={"receive_id_type": "email"},
                headers={"Authorization": "Bearer " + self._token},
                json={
                    "receive_id": self.recipient,
                    "msg_type": "interactive" if content.get("schema") == "2.0" else "post",
                    "content": json.dumps(content, ensure_ascii=False), "uuid": delivery_id,
                },
            )
            value = response.json()
            # Only explicit application rejections are safe to retry manually.
            if (
                response.status_code < 500 and type(value.get("code")) is int
                and value["code"] != 0
            ):
                raise FeishuError("feishu_message_rejected", code=value["code"])
            message_id = value.get("data", {}).get("message_id")
            if (
                not 200 <= response.status_code < 300 or value.get("code") != 0
                or not isinstance(message_id, str) or not message_id.startswith("om_")
            ):
                raise FeishuError("feishu_delivery_unknown", uncertain=True)
            return message_id
        except FeishuError:
            raise
        except Exception:
            raise FeishuError("feishu_delivery_unknown", uncertain=True) from None

    def close(self):
        self._token = None
        self.client.close()

    def update_card(self, message_id, content):
        if not self._token:
            raise FeishuError("feishu_not_authenticated")
        if not re.fullmatch(r"om_[A-Za-z0-9_]+", message_id) or content.get("schema") != "2.0":
            raise FeishuError("feishu_card_update_invalid")
        try:
            response = self.client.patch(
                BASE + "/im/v1/messages/" + message_id,
                headers={"Authorization": "Bearer " + self._token},
                json={"content": json.dumps(content, ensure_ascii=False)},
            )
            value = response.json()
            if (
                response.status_code < 500 and type(value.get("code")) is int
                and value["code"] != 0
            ):
                raise FeishuError("feishu_card_update_rejected", code=value["code"])
            if not 200 <= response.status_code < 300 or value.get("code") != 0:
                raise FeishuError("feishu_card_update_unknown", uncertain=True)
        except FeishuError:
            raise
        except Exception:
            raise FeishuError("feishu_card_update_unknown", uncertain=True) from None
