import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class CorefClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.timeout = int(os.getenv("REQUEST_TIMEOUT", "30"))
        api_key = os.getenv("COREF_API_KEY", "")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def resolve(self, text: str) -> str:
        try:
            response = requests.post(
                f"{self.base_url}/resolve",
                json={"text": text},
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()["resolved_text"]
        except requests.exceptions.Timeout:
            logger.warning("Coreference service timed out — returning original text")
            return text
        except requests.exceptions.RequestException as e:
            logger.warning("Coreference service error: %s — returning original text", e)
            return text

    def health_check(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/health", headers=self.headers, timeout=self.timeout)
            return response.status_code == 200
        except Exception:
            return False