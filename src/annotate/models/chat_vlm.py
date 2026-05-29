"""Generic chat-VLM adapter — free-form Q&A about images.

Wraps any HuggingFace ``transformers`` model that follows the
``AutoModelForImageTextToText`` interface and supports
``apply_chat_template`` (Qwen-VL family, Llava-Next, SmolVLM,
Phi-3.5-Vision, Idefics3, etc.).

Capability: ``ask``. Takes an image (typically a region crop) plus a
free-form question and returns the model's text response.

The default config picks ``Qwen/Qwen2.5-VL-3B-Instruct`` (Apache 2.0,
~6 GB on disk). Users can swap to a smaller / different model by
editing ``[ask.default]`` in models.toml. This adapter handles any
model whose ID matches the registered prefixes below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from annotate.models.base import Adapter, Answer
from annotate.models.registry import register_adapter

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class ChatVlmAdapter(Adapter):
    capabilities = ("ask",)

    # Rough on-disk weights for budget estimation. Anything not listed
    # falls back to 2000 — better to over-estimate than under.
    _SIZE_MB = {
        "Qwen/Qwen2.5-VL-3B-Instruct": 6400,
        "Qwen/Qwen2.5-VL-7B-Instruct": 14600,
        "HuggingFaceTB/SmolVLM-Instruct": 2200,
        "HuggingFaceTB/SmolVLM-256M-Instruct": 500,
        "microsoft/Phi-3.5-vision-instruct": 8000,
    }

    def __init__(self, model_id: str, device: str = "auto", **kwargs):
        super().__init__(model_id, device, **kwargs)
        self.weights_mb_estimate = self._SIZE_MB.get(model_id, 2000)
        self._model = None
        self._processor = None
        self._torch = None
        self._resolved_device = None

    @staticmethod
    def _resolve_device(requested: str) -> str:
        import torch
        if requested == "auto":
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        return requested

    def load(self) -> None:
        if self._loaded:
            return
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        device = self._resolve_device(self.device)
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        self._processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True)
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.model_id,
            torch_dtype=dtype,
            trust_remote_code=True,
        ).to(device)
        self._model.eval()
        self._torch = torch
        self._resolved_device = device
        self._loaded = True

    def unload(self) -> None:
        if not self._loaded:
            return
        del self._model
        del self._processor
        self._model = None
        self._processor = None
        if self._torch is not None and self._resolved_device == "cuda":
            self._torch.cuda.empty_cache()
        self._loaded = False

    def ask(
        self,
        image: "PILImage",
        question: str,
        *,
        max_new_tokens: int = 256,
        system_prompt: str | None = None,
    ) -> Answer:
        if not self._loaded:
            self.load()
        torch = self._torch

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": question},
            ],
        })

        # apply_chat_template produces both the text and any image-token
        # bookkeeping the processor needs. Some processors accept the
        # messages directly via apply_chat_template(..., tokenize=True,
        # return_tensors="pt") — we use the universal two-step path.
        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = self._processor(
            text=[text], images=[image], padding=True, return_tensors="pt",
        )
        inputs = {k: (v.to(self._resolved_device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}

        with torch.no_grad():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )
        # Slice off the prompt tokens so we decode only the response.
        input_len = inputs["input_ids"].shape[1]
        new_tokens = generated[:, input_len:]
        text_out = self._processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        return Answer(
            question=question,
            text=text_out,
            finish_reason="length" if new_tokens.shape[1] >= max_new_tokens else "stop",
            tokens_generated=int(new_tokens.shape[1]),
        )


# Register for known chat-VLM prefixes. Users can add their own by
# editing the config; if a new model_id matches none of these prefixes,
# acquire() will raise NotInstalledError with the missing-factory hint.
for _prefix in (
    "Qwen/Qwen2.5-VL-",
    "Qwen/Qwen2-VL-",
    "HuggingFaceTB/SmolVLM",
    "microsoft/Phi-3.5-vision",
    "llava-hf/llava-",
    "llava-hf/llama3-llava-",
    "HuggingFaceM4/Idefics3-",
):
    register_adapter(_prefix,
                     lambda model_id, device="auto", **kw: ChatVlmAdapter(model_id, device, **kw))
