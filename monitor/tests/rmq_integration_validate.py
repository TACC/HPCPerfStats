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
            saw_sample = True
        else:
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

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)
    print(f"validated {len(seen)} message(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
