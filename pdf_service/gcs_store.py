"""Immutable invoice storage using existing Google application credentials."""
from datetime import timedelta
from hashlib import sha256
from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage


class GCSArtifactStore:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name
        self._bucket = None

    def _blob(self, key):
        if self._bucket is None:
            self._bucket = storage.Client().bucket(self.bucket_name)
        return self._bucket.blob(key)

    def put_if_absent(self, key, content):
        blob = self._blob(key)
        try:
            blob.upload_from_string(content, content_type="application/pdf", if_generation_match=0)
        except PreconditionFailed:
            if sha256(blob.download_as_bytes()).digest() != sha256(content).digest():
                raise ValueError("Existing invoice content differs")

    def document_url(self, artifact):
        return self._blob(artifact.storage_key).generate_signed_url(
            version="v4", expiration=timedelta(minutes=15), method="GET")
