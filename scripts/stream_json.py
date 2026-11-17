"""Dependency-free bounded streaming reader of top-level AltStore JSON apps array."""
import codecs
import json

CHUNK = 65536
MAX_ITEM_CHARS = 8 * 1024 * 1024

class Reader:
    def __init__(self, stream):
        self.stream = stream
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.json_decoder = json.JSONDecoder()
        self.buffer = ""
        self.pos = 0
        self.exhausted = False

    def fill(self):
        if self.exhausted:
            raise ValueError("Unexpected end of JSON")
        if self.pos > CHUNK:
            self.buffer = self.buffer[self.pos:]
            self.pos = 0
        chunk = self.stream.read(CHUNK)
        if chunk:
            self.buffer += self.decoder.decode(chunk)
        else:
            self.buffer += self.decoder.decode(b"", final=True)
            self.exhausted = True

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.exhausted:
                raise ValueError("Unexpected end of JSON")
            self.fill()

    def expect(self, token):
        value = self.peek()
        if value != token:
            raise ValueError(f"Expected {token!r}; got {value!r}")
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.json_decoder.raw_decode(self.buffer, self.pos)
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.exhausted:
                    raise ValueError("Incomplete JSON value") from None
                if len(self.buffer) - self.pos > MAX_ITEM_CHARS:
                    raise ValueError("Single JSON field exceeds 8 MiB limit")
                self.fill()

def iter_apps(stream):
    reader = Reader(stream)
    reader.expect("{")
    first = True
    while True:
        if reader.peek() == "}":
            raise ValueError("Missing apps array")
        if not first:
            reader.expect(",")
        key = reader.value()
        if not isinstance(key, str):
            raise ValueError("Invalid JSON object key")
        reader.expect(":")
        if key == "apps":
            reader.expect("[")
            first_app = True
            while True:
                if reader.peek() == "]":
                    reader.pos += 1
                    return
                if not first_app:
                    reader.expect(",")
                yield reader.value()
                first_app = False
        else:
            reader.value()
        first = False
