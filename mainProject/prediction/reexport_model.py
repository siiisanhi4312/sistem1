"""
Re-export Trained Model to TFLite

This script:
1. Loads the trained Keras model
2. Loads class labels
3. Converts the model to TFLite
4. Saves the TFLite model
5. Verifies the TFLite model using LiteRT
"""

import os

# Reduce TensorFlow console messages
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import tensorflow as tf
import json
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

TRAINED_MODEL_PATH = Path("model/best_seaweed_model.keras")
OUTPUT_TFLITE_PATH = Path("model/shell_model8.tflite")
LABELS_PATH = Path("model/labels.json")


# ============================================================
# HEADER
# ============================================================

print("=" * 80)
print("🔄 MODEL RE-EXPORT TO TFLITE")
print("=" * 80)


# ============================================================
# CHECK TRAINED MODEL
# ============================================================

if not TRAINED_MODEL_PATH.exists():
    print(
        f"\n❌ ERROR: Trained model not found at "
        f"{TRAINED_MODEL_PATH}"
    )

    print(
        "   Make sure you're running this script "
        "from the prediction/backend directory."
    )

    raise SystemExit(1)


print(
    f"\n✓ Found trained model: "
    f"{TRAINED_MODEL_PATH}"
)

print(
    f"  File size: "
    f"{TRAINED_MODEL_PATH.stat().st_size / 1024 / 1024:.2f} MB"
)


# ============================================================
# LOAD KERAS MODEL
# ============================================================

print("\n⏳ Loading Keras model...")

try:

    model = tf.keras.models.load_model(
        str(TRAINED_MODEL_PATH),
        compile=False
    )

    print("✓ Model loaded successfully")

    print(
        f"  Input shape: "
        f"{model.input_shape}"
    )

    print(
        f"  Output shape: "
        f"{model.output_shape}"
    )

except Exception as e:

    print(
        f"❌ ERROR loading model: {e}"
    )

    raise SystemExit(1)


# ============================================================
# LOAD CLASS LABELS
# ============================================================

class_names = None

if LABELS_PATH.exists():

    try:

        with open(
            LABELS_PATH,
            "r",
            encoding="utf-8"
        ) as f:

            class_names = json.load(f)

        print(
            f"\n✓ Class labels found: "
            f"{class_names}"
        )

    except Exception as e:

        print(
            f"\n❌ ERROR reading labels.json: {e}"
        )

        raise SystemExit(1)

else:

    print(
        f"\n⚠️ labels.json not found at "
        f"{LABELS_PATH}"
    )


# ============================================================
# CHECK CLASS COUNT
# ============================================================

try:

    model_num_classes = int(
        model.output_shape[-1]
    )

    print(
        f"\n✓ Model output classes: "
        f"{model_num_classes}"
    )

    if class_names is not None:

        if len(class_names) != model_num_classes:

            print("\n❌ ERROR: CLASS COUNT MISMATCH")

            print(
                f"   Model output: "
                f"{model_num_classes}"
            )

            print(
                f"   labels.json: "
                f"{len(class_names)}"
            )

            print(
                "\nThe labels.json file must contain "
                "exactly the same number of classes "
                "as the model output."
            )

            raise SystemExit(1)

        print(
            "✓ labels.json matches model output"
        )

except Exception as e:

    print(
        f"\n❌ ERROR checking class count: {e}"
    )

    raise SystemExit(1)


# ============================================================
# CONVERT TO TFLITE
# ============================================================

print("\n" + "=" * 80)
print("⏳ CONVERTING TO TFLITE")
print("=" * 80)

try:

    converter = (
        tf.lite.TFLiteConverter
        .from_keras_model(model)
    )

    # Enable standard TFLite optimization
    converter.optimizations = [
        tf.lite.Optimize.DEFAULT
    ]

    # Use standard TFLite built-in operations.
    #
    # SELECT_TF_OPS is intentionally NOT enabled because
    # the model already converts successfully using
    # standard TFLite operations.

    converter.target_spec.supported_ops = [
        tf.lite.OpsSet.TFLITE_BUILTINS
    ]

    print(
        "\n  Optimization:"
        " tf.lite.Optimize.DEFAULT"
    )

    print(
        "  Supported operations:"
        " TFLITE_BUILTINS"
    )

    tflite_model = converter.convert()

    print(
        "\n✓ Conversion successful!"
    )

    print(
        f"  TFLite model size: "
        f"{len(tflite_model) / 1024 / 1024:.2f} MB"
    )

except Exception as e:

    print(
        f"\n❌ ERROR during conversion:"
    )

    print(e)

    raise SystemExit(1)


# ============================================================
# SAVE TFLITE MODEL
# ============================================================

print(
    f"\n⏳ Saving TFLite model to "
    f"{OUTPUT_TFLITE_PATH}..."
)

OUTPUT_TFLITE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True
)

try:

    with open(
        OUTPUT_TFLITE_PATH,
        "wb"
    ) as f:

        bytes_written = f.write(
            tflite_model
        )

    print(
        "\n✓ TFLite model saved successfully!"
    )

    print(
        f"  File size: "
        f"{OUTPUT_TFLITE_PATH.stat().st_size / 1024 / 1024:.2f} MB"
    )

    print(
        f"  Bytes written: "
        f"{bytes_written / 1024 / 1024:.2f} MB"
    )

except Exception as e:

    print(
        f"\n❌ ERROR saving TFLite model: {e}"
    )

    raise SystemExit(1)


# ============================================================
# VERIFY TFLITE MODEL USING LITERT
# ============================================================

print("\n" + "=" * 80)
print("⏳ VERIFYING TFLITE MODEL")
print("=" * 80)

try:

    from ai_edge_litert.interpreter import Interpreter

    print(
        "\n✓ Using LiteRT interpreter"
    )

    interpreter = Interpreter(
        model_path=str(
            OUTPUT_TFLITE_PATH
        )
    )

    interpreter.allocate_tensors()

    input_details = (
        interpreter.get_input_details()
    )

    output_details = (
        interpreter.get_output_details()
    )

    print(
        "\n✓ TFLite model verified successfully!"
    )


    # --------------------------------------------------------
    # INPUT
    # --------------------------------------------------------

    print("\n  INPUT TENSOR:")

    print(
        f"    Index: "
        f"{input_details[0]['index']}"
    )

    print(
        f"    Shape: "
        f"{input_details[0]['shape']}"
    )

    print(
        f"    Dtype: "
        f"{input_details[0]['dtype']}"
    )


    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    print("\n  OUTPUT TENSOR:")

    print(
        f"    Index: "
        f"{output_details[0]['index']}"
    )

    print(
        f"    Shape: "
        f"{output_details[0]['shape']}"
    )

    print(
        f"    Dtype: "
        f"{output_details[0]['dtype']}"
    )


    # --------------------------------------------------------
    # VERIFY OUTPUT CLASS COUNT
    # --------------------------------------------------------

    tflite_num_classes = int(
        output_details[0]["shape"][-1]
    )

    if tflite_num_classes != model_num_classes:

        print(
            "\n❌ ERROR: TFLITE CLASS COUNT "
            "DOES NOT MATCH KERAS MODEL"
        )

        print(
            f"  Keras model : "
            f"{model_num_classes}"
        )

        print(
            f"  TFLite model: "
            f"{tflite_num_classes}"
        )

        raise SystemExit(1)

    print(
        f"\n✓ TFLite output matches "
        f"{model_num_classes} classes"
    )


except Exception as e:

    print(
        f"\n❌ ERROR verifying TFLite model:"
    )

    print(e)

    raise SystemExit(1)


# ============================================================
# FINAL RESULT
# ============================================================

print("\n" + "=" * 80)
print("✅ MODEL RE-EXPORT COMPLETE!")
print("=" * 80)

print(
    f"""
Summary:

  ✓ Keras model:
      {TRAINED_MODEL_PATH}

  ✓ TFLite model:
      {OUTPUT_TFLITE_PATH}

  ✓ Input shape:
      {input_details[0]["shape"]}

  ✓ Input dtype:
      {input_details[0]["dtype"]}

  ✓ Output shape:
      {output_details[0]["shape"]}

  ✓ Output dtype:
      {output_details[0]["dtype"]}

  ✓ Number of classes:
      {model_num_classes}

  ✓ Labels:
      {class_names}

  ✓ TFLite verification:
      PASSED

The TFLite model is ready for use.
"""
)