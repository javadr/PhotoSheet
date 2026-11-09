"""Image effects as plain functions on RGB/L PIL images. Applied in list order."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps


def gamma(im: Image.Image, gamma: float = 1.0) -> Image.Image:
    if gamma <= 0:
        raise ValueError("gamma must be > 0")
    lut = [round(255 * (i / 255) ** (1 / gamma)) for i in range(256)]
    return im.point(lut * len(im.getbands()))


def desaturate(im: Image.Image) -> Image.Image:
    return ImageOps.grayscale(im).convert(im.mode)


def temperature(im: Image.Image, change: int = 0) -> Image.Image:
    """Warm (+) / cool (-). change is kelvin-ish, -2000..2000.

    shortcut: per-channel gain, not the original's lcms white-point transform;
    upgrade to an ImageCms virtual profile if exact parity matters.
    """
    k = max(-1.0, min(1.0, change / 2000.0)) * 0.25
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    a[..., 0] *= 1 + k
    a[..., 2] *= 1 - k
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def unsharp(im: Image.Image, radius: float = 3.0, amount: float = 1.5) -> Image.Image:
    return im.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=0))


EFFECTS = {"Gamma": gamma, "Desaturate": desaturate, "Temperature": temperature, "UnsharpMask": unsharp}


def apply_effects(im: Image.Image, effects: list) -> Image.Image:
    for name, params in effects:
        try:
            fn = EFFECTS[name]
        except KeyError:
            raise ValueError(f"unknown effect {name!r}") from None
        im = fn(im, **params)
    return im
