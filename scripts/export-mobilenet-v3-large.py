#!/usr/bin/env python3
"""Export MobileNetV3Large as a TensorFlow SavedModel for KServe."""

from __future__ import annotations

import argparse
from pathlib import Path

import tensorflow as tf


DEFAULT_OUTPUT_DIR = "/pv/mobilenet-v3-large/1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download ImageNet MobileNetV3Large weights and export a SavedModel.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"SavedModel version directory to write. Default: {DEFAULT_OUTPUT_DIR}",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = tf.keras.applications.MobileNetV3Large(
        weights="imagenet",
        include_top=True,
        include_preprocessing=True,
        input_shape=(224, 224, 3),
    )

    # Build the serving signature before export so TensorFlow Serving sees a
    # stable float32 NHWC input.
    sample = tf.zeros((1, 224, 224, 3), dtype=tf.float32)
    _ = model(sample, training=False)

    if hasattr(model, "export"):
        model.export(str(output_dir))
    else:
        tf.saved_model.save(model, str(output_dir))

    print(f"Exported MobileNetV3Large SavedModel to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
