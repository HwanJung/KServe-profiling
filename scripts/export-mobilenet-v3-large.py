#!/usr/bin/env python3
"""Export MobileNetV3Large as ONNX for KServe."""

from __future__ import annotations

import argparse
from pathlib import Path

import tensorflow as tf
import tf2onnx


DEFAULT_OUTPUT_PATH = "/pv/onnx-repository/mobilenet-v3-large/1/model.onnx"
DEFAULT_OPSET = 17


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download ImageNet MobileNetV3Large weights and export an ONNX model.",
    )
    parser.add_argument(
        "--output-path",
        default=DEFAULT_OUTPUT_PATH,
        help=f"ONNX model path to write. Default: {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=DEFAULT_OPSET,
        help=f"ONNX opset version. Default: {DEFAULT_OPSET}",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    base_model = tf.keras.applications.MobileNetV3Large(
        weights="imagenet",
        include_top=True,
        include_preprocessing=True,
        input_shape=(224, 224, 3),
    )
    inputs = tf.keras.Input(shape=(224, 224, 3), name="input", dtype=tf.float32)
    outputs = base_model(inputs, training=False)
    model = tf.keras.Model(inputs=inputs, outputs=outputs, name="mobilenet_v3_large")

    input_signature = (
        tf.TensorSpec((None, 224, 224, 3), tf.float32, name="input"),
    )
    tf2onnx.convert.from_keras(
        model,
        input_signature=input_signature,
        opset=args.opset,
        output_path=str(output_path),
    )

    print(f"Exported MobileNetV3Large ONNX model to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
