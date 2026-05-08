#!/usr/bin/env python3
import argparse
import json
import sys
import time

import pika


def parse_args():
    p = argparse.ArgumentParser(description="Validate monitor payloads from RabbitMQ")
    p.add_argument("--host", required=True)
    p.add_argument("--port", required=True, type=int)
    p.add_argument("--queue", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--vhost", required=True)
    p.add_argument("--min-messages", required=True, type=int)
    p.add_argument("--timeout-seconds", required=True, type=float)
    p.add_argument("--out-json", required=True)
    return p.parse_args()


def parse_host_like_listend(message):
    if not message:
        raise ValueError("empty payload")
    if message[0] == "$":
        parts = message.split("\n")
        if len(parts) < 2:
            raise ValueError("malformed '$' payload: missing host line")
        host_parts = parts[1].split()
        if len(host_parts) < 2:
            raise ValueError("malformed '$' payload: host line missing token")
        return host_parts[1], "schema"
    fields = message.split()
    if len(fields) < 3:
        raise ValueError("malformed sample payload: fewer than 3 fields")
    return fields[2], "sample"


def validate_sample_row(message):
    fields = message.split()
    if len(fields) < 3:
        raise ValueError("sample payload has fewer than 3 tokens")
    try:
        float(fields[0])
    except ValueError as exc:
        raise ValueError("sample timestamp token is not numeric") from exc
    if not fields[1]:
        raise ValueError("sample jobid token empty")


def parse_schema_counts(message):
    counts = {}
    for raw in message.splitlines():
        line = raw.strip()
        if not line or not line.startswith("!"):
            continue
        fields = line[1:].split()
        if len(fields) < 2:
            raise ValueError(f"malformed schema line: {line!r}")
        type_name = fields[0]
        counts[type_name] = len(fields) - 1
    if not counts:
        raise ValueError("schema payload did not contain any '!' schema lines")
    return counts


def validate_sample_rows_against_schema(message, schema_counts):
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty sample payload body")

    header = lines[0].split()
    if len(header) < 3:
        raise ValueError("sample header line missing timestamp/jobid/host")
    try:
        float(header[0])
    except ValueError as exc:
        raise ValueError("sample header timestamp is not numeric") from exc

    validated_rows = 0
    unknown_types = []
    for line in lines[1:]:
        if line[0] in ("%", "#", "$", "!", "@"):
            continue
        fields = line.split()
        if len(fields) < 2:
            raise ValueError(f"malformed sample row: {line!r}")
        # A payload can contain multiple sample blocks; each block starts with
        # "<timestamp> <jobid> <host>".
        try:
            float(fields[0])
            if len(fields) >= 3:
                continue
        except ValueError:
            pass
        type_name = fields[0]
        if type_name not in schema_counts:
            unknown_types.append(type_name)
            continue
        expected = schema_counts[type_name]
        min_tokens = 1 + 1 + expected  # type + device + values
        if len(fields) < min_tokens:
            raise ValueError(
                f"row for type {type_name!r} too short for expected {expected} values: {line!r}"
            )
        value_tokens = fields[-expected:] if expected > 0 else []
        value_count = len(value_tokens)
        if value_count != expected:
            raise ValueError(
                f"row for type {type_name!r} has {value_count} values, expected {expected}: {line!r}"
            )
        validated_rows += 1

    if unknown_types:
        raise ValueError(
            "sample rows referenced types absent from schema: "
            + ", ".join(sorted(set(unknown_types)))
        )
    if validated_rows == 0:
        raise ValueError("sample payload did not contain any schema-validated metric rows")
    return validated_rows


def main():
    args = parse_args()
    creds = pika.PlainCredentials(args.user, args.password)
    params = pika.ConnectionParameters(
        host=args.host,
        port=args.port,
        virtual_host=args.vhost,
        credentials=creds,
        connection_attempts=3,
        retry_delay=1,
        socket_timeout=5,
        stack_timeout=10,
        blocked_connection_timeout=10,
    )

    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=args.queue, durable=True, passive=False)

    deadline = time.time() + args.timeout_seconds
    seen = []
    saw_schema = False
    saw_sample = False
    schema_counts = {}
    validated_row_total = 0

    while time.time() < deadline and len(seen) < args.min_messages:
        method, properties, body = channel.basic_get(queue=args.queue, auto_ack=True)
        if method is None:
            time.sleep(0.2)
            continue

        try:
            payload = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssertionError("message is not UTF-8 decodable") from exc

        host, msg_type = parse_host_like_listend(payload)
        if not host:
            raise AssertionError("parsed host is empty")
        if msg_type == "sample":
            validate_sample_row(payload)
            if not schema_counts:
                raise AssertionError("sample payload arrived before schema was captured")
            validated_row_total += validate_sample_rows_against_schema(payload, schema_counts)
            saw_sample = True
        else:
            schema_counts.update(parse_schema_counts(payload))
            saw_schema = True

        if properties is None:
            raise AssertionError("missing AMQP properties")
        if properties.content_type != "text/plain":
            raise AssertionError(
                f"unexpected content_type {properties.content_type!r}; expected 'text/plain'"
            )
        if properties.delivery_mode != 2:
            raise AssertionError(
                f"unexpected delivery_mode {properties.delivery_mode!r}; expected 2"
            )

        seen.append({"type": msg_type, "host": host, "len": len(payload)})

    connection.close()

    if len(seen) < args.min_messages:
        raise AssertionError(
            f"expected at least {args.min_messages} message(s), got {len(seen)}"
        )
    if not saw_schema:
        raise AssertionError("did not observe any schema ('$') payload")
    if not saw_sample:
        raise AssertionError("did not observe any sample payload")
    if validated_row_total <= 0:
        raise AssertionError("no sample metric rows were validated against schema")

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)
    print(f"validated {len(seen)} message(s), {validated_row_total} schema-checked sample rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
