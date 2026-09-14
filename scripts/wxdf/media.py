"""Native media embedding for normalized DOCX image tokens."""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import Any, Mapping

from docx.oxml.shape import CT_Inline
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.parts.image import ImagePart
from docx.shared import Emu


class MediaEmbedder:
    """Embed image payloads while preserving native media bytes."""

    _NATIVE_TYPES = {
        "image/emf": ".emf",
        "image/x-emf": ".emf",
        "image/wmf": ".wmf",
        "image/x-wmf": ".wmf",
    }

    def __init__(
        self,
        resource_descriptors: Mapping[str, Any],
        resources: Mapping[str, Any],
        *,
        error_type: type[Exception] = RuntimeError,
    ) -> None:
        self.resource_descriptors = resource_descriptors
        self.resources = resources
        self.error_type = error_type
        self._native_cache: dict[str, tuple[ImagePart, str]] = {}

    def embed(self, run: Any, image_token: Mapping[str, Any]) -> Any:
        resource_id = str(image_token.get("resource_id") or "")
        descriptor = self.resource_descriptors.get(resource_id)
        payload = self.resources.get(resource_id)
        if not isinstance(descriptor, Mapping):
            raise self.error_type(f"resource {resource_id!r} is unavailable for rendering")
        if payload is None:
            raise self.error_type(f"resource {resource_id!r} has no renderable bytes or path")

        media_type = self._media_type(descriptor, resource_id)
        display_size = image_token.get("display_size")
        dimensions = self._dimensions(display_size, resource_id)
        if media_type in self._NATIVE_TYPES:
            if dimensions is None:
                raise self.error_type(
                    f"resource {resource_id!r} requires display_size for native media {media_type}"
                )
            blob = self._read_payload(payload, resource_id)
            return self._embed_native(run, resource_id, blob, media_type, dimensions, descriptor)

        if not self._is_supported_decodable(media_type, descriptor, resource_id) and dimensions is None:
            raise self.error_type(
                f"resource {resource_id!r} format cannot be detected; display_size is required"
            )
        width, height = dimensions or (None, None)
        if isinstance(payload, (bytes, bytearray)):
            return run.add_picture(io.BytesIO(bytes(payload)), width=Emu(width) if width else None, height=Emu(height) if height else None)
        if isinstance(payload, (str, Path)):
            return run.add_picture(str(payload), width=Emu(width) if width else None, height=Emu(height) if height else None)
        raise self.error_type(f"resource {resource_id!r} has no renderable bytes or path")

    def _media_type(self, descriptor: Mapping[str, Any], resource_id: str) -> str:
        media_type = str(descriptor.get("media_type") or "").lower()
        if media_type:
            return media_type
        source_ref = descriptor.get("source_ref")
        part = source_ref.get("part") if isinstance(source_ref, Mapping) else None
        name = str(part or resource_id)
        guessed, _ = mimetypes.guess_type(name)
        return str(guessed or "")

    def _dimensions(self, value: Any, resource_id: str) -> tuple[int, int] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise self.error_type(f"resource {resource_id!r} has invalid display_size")
        width = value.get("width_emu")
        height = value.get("height_emu")
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            raise self.error_type(f"resource {resource_id!r} has invalid display_size.width_emu")
        if isinstance(height, bool) or not isinstance(height, int) or height < 1:
            raise self.error_type(f"resource {resource_id!r} has invalid display_size.height_emu")
        return width, height

    def _read_payload(self, payload: Any, resource_id: str) -> bytes:
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if isinstance(payload, (str, Path)):
            try:
                return Path(payload).read_bytes()
            except OSError as exc:
                raise self.error_type(f"resource {resource_id!r} cannot be read") from exc
        raise self.error_type(f"resource {resource_id!r} has no renderable bytes or path")

    def _embed_native(
        self,
        run: Any,
        resource_id: str,
        blob: bytes,
        media_type: str,
        dimensions: tuple[int, int],
        descriptor: Mapping[str, Any],
    ) -> Any:
        package = run.part.package
        suffix = self._NATIVE_TYPES[media_type]
        cached = self._native_cache.get(resource_id)
        if cached is None:
            partname = package.next_partname(f"/word/media/image%d{suffix}")
            image_part = ImagePart(partname, media_type, blob)
            relationship_id = run.part.relate_to(image_part, RT.IMAGE)
            self._native_cache[resource_id] = (image_part, relationship_id)
        else:
            image_part, relationship_id = cached
        width, height = dimensions
        source_ref = descriptor.get("source_ref")
        part = source_ref.get("part") if isinstance(source_ref, Mapping) else None
        filename = Path(str(part or f"image{suffix}")).name
        inline = CT_Inline.new_pic_inline(
            run.part.next_id,
            relationship_id,
            filename,
            Emu(width),
            Emu(height),
        )
        return run._r.add_drawing(inline)

    def _is_supported_decodable(
        self,
        media_type: str,
        descriptor: Mapping[str, Any],
        resource_id: str,
    ) -> bool:
        if media_type.startswith("image/"):
            return True
        source_ref = descriptor.get("source_ref")
        part = source_ref.get("part") if isinstance(source_ref, Mapping) else None
        return bool(mimetypes.guess_type(str(part or resource_id))[0])
