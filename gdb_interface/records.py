from __future__ import annotations

import ast
from enum import Enum
import itertools
import re
import sys
from typing import IO, Any, Callable, Iterator, TypeVar, cast, overload


class GDBRecord:
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        self._source = source
        self._content = content.strip().lstrip(b'^*+=~&@')
        self._num = num
        
    @property
    def num(self) -> int:
        return self._num
        
    def __repr__(self) -> str:
        return self._content.__repr__()
    
    def __str__(self) -> str:
        return self._content.decode(errors='ignore')
    
    @overload
    def __getitem__(self, key: int) -> int: ...
    
    @overload
    def __getitem__(self, key: slice) -> bytes: ...
    
    def __getitem__(self, key: int | slice) -> int | bytes:
        return self._content[key]
    
    def startswith(self, prefix: bytes) -> bool:
        return self._content.startswith(prefix)
    
    def endswith(self, prefix: bytes) -> bool:
        return self._content.endswith(prefix)
    
    def __bytes__(self) -> bytes:
        return self._content
    
    def __eq__(self, other: GDBRecord | bytes | object) -> bool:
        if isinstance(other, bytes):
            return self._content == other
        if isinstance(other, GDBRecord):
            return self._content == other._content and self._source == other._source
        return False

GDBRecord_T = TypeVar('GDBRecord_T', bound=GDBRecord)

class GDBDictRecord(GDBRecord):
    _key_value_regex = re.compile(rb'([\w|-]+)=((?:\{.*\}|\[.*\]|"((?:[^"\\]|\\\\.)*?)"))')
    _whitespace_regex = re.compile(rb'\s+')
    
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        temp = self._content.split(b',', 1)
        self._result_name: bytes = temp[0]
        self._data_info: bytes = temp[1] if len(temp) > 1 else b''
        self._data: dict[str, Any] | None = None if len(temp) > 1 else dict()
    
    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = cast(dict, self._parse_data(self._data_info)[0])
        return self._data
    
    def satisfies(self, **kwargs):
        for key, val in kwargs:
            if self.data.get(key) != val:
                return False
        return True
    
    @classmethod
    def _bracket_span(cls, data: bytes, start: int, end: int, open_bracket: bytes, close_bracket: bytes) -> tuple[int, int] | None:
        bracket_count = 0
        for i in range(start, end):
            if data[i] == open_bracket[0]:
                bracket_count += 1
            elif data[i] == close_bracket[0]:
                bracket_count -= 1
                if bracket_count == 0:
                    return (start, i+1)
        return None
    
    @classmethod
    def _parse_data(cls, data: bytes, start=0, end=sys.maxsize) -> tuple[dict | list, int]:
        spaces = cls._whitespace_regex.match(data, start, end)
        if spaces is not None and spaces.end() > start:
            return cls._parse_data(data, spaces.end(), end)
        
        if data[start] == ord(b'['):
            bracket_span = cls._bracket_span(data, start, end, b'[', b']')
            if bracket_span is not None:
                res = []
                end = bracket_span[0]+1
                while end < bracket_span[1] - 1:
                    d, end = cls._parse_data(data, end, bracket_span[1]-1)
                    res.append(d)
                return res, bracket_span[1] + 1
            else: raise ValueError("Mismatched square brackets in data")
            
        if data[start] == ord(b'{'):
            bracket_span = cls._bracket_span(data, start, end, b'{', b'}')
            if bracket_span is not None:
                d, end = cls._parse_data(data, bracket_span[0]+1, bracket_span[1]-1)
                return d, bracket_span[1] + 1
            else: raise ValueError("Mismatched curly brackets in data")
        
        res = dict()
        while start < end:
            matched = cls._key_value_regex.match(data, start, end)
            if not matched:
                break
            key, value = matched.group(1), matched.group(2)
            if value.lstrip()[0] in [ord(b'{'), ord(b'[')]:
                res[key.decode()], _ = cls._parse_data(value, 0, matched.end() - matched.start())
            else:
                res[key.decode()] = ast.literal_eval(value.decode())
            start = matched.end() + 1

        return res, end
        
class GDBStreamRecord(GDBRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        
    def __str__(self) -> str:
        return ast.literal_eval(self._content.decode(errors='ignore'))

class GDBResultType(Enum):
    DONE = 'done'
    RUNNING = 'running'
    ERROR = 'error'
    EXIT = 'exit'
    
class GDBProgramState(Enum):
    STOPPED = 'stopped'
    RUNNING = 'running'

class GDBResultRecord(GDBDictRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        self.result = GDBResultType(self._result_name.decode())
        
class GDBExecRecord(GDBDictRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        self.state = GDBProgramState(self._result_name.decode())
        self.reason = cast(str | None, self.data.get('reason', None))
        
class GDBStatusRecord(GDBDictRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        
class GDBNotifyRecord(GDBDictRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        self.notif_type = self._result_name.decode()

class GDBConsoleRecord(GDBStreamRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        
class GDBLogRecord(GDBStreamRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)

class GDBInferiorRecord(GDBStreamRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        
    def __str__(self) -> str:
        return self._content.decode(errors='ignore')
    
class GDBSequenceEnd(GDBRecord):
    def __init__(self, source: IO[bytes], content: bytes, num: int):
        super().__init__(source, content, num)
        
class GDBCmdOutput:
    def __init__(self, records: list[GDBRecord]):
        self.records = records
        self._result = None
        self._exec_result = None
    
    @property
    def result(self) -> GDBResultRecord:
        if self._result is None:
            self._get_result()
        if self._result is None:
            raise Exception("No result record found in command output")
        return self._result
    
    @property
    def exec_result(self) -> GDBExecRecord:
        if self._exec_result is None:
            self._get_result()
        if self._exec_result is None:
            raise Exception("No execution result record found in command output")
        return self._exec_result
    
    def _get_result(self, max_iters: int = 5):
        for record in itertools.islice(reversed(self.records), max_iters):
            if isinstance(record, GDBResultRecord):
                self._result = record
            elif isinstance(record, GDBExecRecord):
                self._exec_result = record
            if self._result and self._exec_result:
                break
    
    def __str__(self) -> str:
        return '\n'.join(str(record) for record in self.records)
    
    def extend(self, records: list[GDBRecord]):
        self.records.extend(records)
        self._result, self._exec_result = None, None
                
    def find_record(self, record_type: type[GDBRecord_T], predicate: Callable[[GDBRecord_T], bool] = lambda _: True, reverse: bool = False) -> Iterator[GDBRecord_T]:
        return cast(Iterator[GDBRecord_T], filter(lambda r: isinstance(r, record_type) and predicate(r), reversed(self.records) if reverse else self.records))
    
class Breakpoint:
    def __init__(self, data: dict[str, Any]):
        self._id: str = data['number']
        self._info = data
        self._callbacks: list[Callable[[GDBExecRecord], None]] = []
    
    @property
    def id(self):
        return self._id
    
    @property
    def info(self):
        return self._info
    
    def register_on_hit(self, callback: Callable[[GDBExecRecord], None]):
        self._callbacks.append(callback)
        
    def run_on_hit(self, record: GDBExecRecord):
        for cb in self._callbacks:
            cb(record)

def to_record(src: IO[bytes], line: bytes, num: int, default_type: type[GDBRecord] = GDBRecord) -> GDBRecord:
    if line.startswith(b'(gdb)'):
        return GDBSequenceEnd(src, line, num)
    
    return {b'^': GDBResultRecord,
            b'*': GDBExecRecord,
            b'+': GDBStatusRecord,
            b'=': GDBNotifyRecord,
            b'~': GDBConsoleRecord,
            b'&': GDBLogRecord,
            b'@': GDBInferiorRecord
            }.get(line[:1], default_type)(src, line, num)