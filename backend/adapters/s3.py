from pathlib import Path
import logging
import boto3
from botocore.exceptions import ClientError
from pydantic import SecretStr

from configs.config import Config


class S3Client:
    def __init__(
        self,
        bucket_name: str,
        aws_access_key_id: SecretStr | None = None,
        aws_secret_access_key: SecretStr | None = None,
        endpoint_url: str | None = None,
    ):
        self.bucket_name = bucket_name
        self.client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key_id.get_secret_value()
            if aws_access_key_id
            else None,
            aws_secret_access_key=aws_secret_access_key.get_secret_value()
            if aws_secret_access_key
            else None,
            endpoint_url=endpoint_url,
        )
        self.logger = logging.getLogger("S3Client")

    def _normalize_key(self, key: str | Path) -> str:
        return str(key).replace("\\", "/")

    def is_file_exist(self, s3_key: Path) -> bool:
        """
        Check if a file exists in S3.

        Args:
            s3_key (Path): S3 key to check.
        Returns:
            bool: True if the file exists, False otherwise.
        """
        key = self._normalize_key(s3_key)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")

            # Object not found (AWS S3 or S3-compatible)
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False

            # Any other error — real problem
            self.logger.error(
                f"S3 head_object failed: key='{key}' error={e.response}"
            )
            raise e from e
        except Exception as e:
            self.logger.error(
                f"S3 unexpected error: key='{key}' error='{repr(e)}'"
            )
            raise e from e

    def get_presigned_url(self, s3_key: Path, expires_in: int = 3600) -> str:
        """
        Generate a presigned URL for an S3 object.

        Args:
            s3_key (Path): S3 key for which to generate the URL.
            expires_in (int): Expiration time in seconds for the URL.
        Returns:
            str: Presigned URL.
        """
        key = self._normalize_key(s3_key)
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            )
            self.logger.info(f"Generated presigned URL: key='{key}'")
            return url
        except Exception as e:
            self.logger.error(
                f"Failed to generate presigned URL: key='{key}' error={repr(e)}"
            )
            raise e from e

    def list_files_in_folder(self, folder: Path) -> list[str]:
        """
        List all files in a specified S3 folder (prefix).

        Args:
            folder (Path): S3 folder (prefix).
        Returns:
            list[str]: List of file keys.
        """
        prefix = self._normalize_key(folder)
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            files = []
            for page in page_iterator:
                for obj in page.get("Contents", []):
                    files.append(obj["Key"])
            return files
        except Exception as e:
            self.logger.error(f"Failed to list files in folder: prefix='{prefix}' error='{repr(e)}'")
            raise e from e
        

def get_s3_client(config: Config) -> S3Client:
    return S3Client(
        bucket_name=config.DATA_BUCKET_NAME,
        aws_access_key_id=config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
    )