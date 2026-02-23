import threading
from typing import IO, Callable, Generator, Generic, TypeVar

from .circular_queue import CircularQueue

T = TypeVar('T')

class LockedVar(Generic[T]):
    def __init__(self, value: T):
        self._value = value
        self._lock = threading.Lock()
    
    def __enter__(self) -> T:
        return self.aquire()
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
        
    def aquire(self) -> T:
        self._lock.acquire()
        return self._value
    
    def release(self) -> None:
        self._lock.release()

class BlockingIOReader(Generic[T]):
    def __init__(self, file: IO[bytes], capacity: int = 256, _convert_output: Callable[[IO[bytes], bytes], T | None] = lambda _, x: x):
        self._file = file
        self._output: LockedVar[CircularQueue[T]] = LockedVar(CircularQueue[T](capacity))
        self._convert_output = _convert_output
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
    
    @property
    def output(self) -> LockedVar[CircularQueue[T]]:
        return self._output
    
    def read_line(self, block: bool = False) -> T | None:
        line = None
        if block:
            output = self._output.aquire()
            while len(output) == 0:
                self._output.release()
                output = self._output.aquire()
            line = output.pop()
            self._output.release()
        else:
            with self._output as output:
                line = output.pop()
        
        return line
    
    def read_lines(self, block: bool = False) -> Generator[T, None, None]:
        line = self.read_line(block)
        while line:
            yield line
            line = self.read_line(block)
        
        return None
    
    def read_until(self, predicate: Callable[[T], bool], block: bool = False) -> T | None:
        for line in self.read_lines(block):
            if predicate(line):
                return line
        
        return None
        
    def _reader(self) -> None:
        for line in self._file:
            converted = self._convert_output(self._file, line)
            if converted:
                with self._output as output:
                    output.push(converted)
    
    def close(self):
        if not self._file.closed:
            self._file.close()
      
    def __del__(self):
        self.close()