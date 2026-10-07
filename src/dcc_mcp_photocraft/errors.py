"""Bounded, path-free adapter errors."""


class AdapterError(Exception):
    def __init__(self, code: str, *, indeterminate: bool = False):
        super().__init__(code)
        self.code = code
        self.indeterminate = indeterminate
