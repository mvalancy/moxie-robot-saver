# `proto/` — protocol schema source (for the toolkit)

The `.proto` schema the toolkit compiles into Python bindings. This is a copy of the canonical
[recovered protos](../../../docs/reverse-engineering/protocol/recovered-proto/) — **120 files, 382 messages** —
reconstructed from the robot firmware **v3.6.4-Zephyr / OTA v24.10.803**.

The `embodied/…` subpackages mirror the firmware's proto layout (generated tree; no per-folder READMEs).
Regenerate the bindings with `python -m grpc_tools.protoc --proto_path=proto --python_out=moxie_toolkit $(find proto -name '*.proto')`.
See the browsable [protocol catalog](../../../docs/reverse-engineering/protocol/proto-catalog.md).
