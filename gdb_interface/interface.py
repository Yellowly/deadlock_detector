import os
import pty
from subprocess import Popen
import subprocess
from typing import IO, Any, Callable, Generator, TypeVar, cast

from .records import Breakpoint, GDBCmdOutput, GDBExecRecord, GDBInferiorRecord, GDBRecord, GDBRecord_T, GDBResultType, GDBSequenceEnd, to_record
from .io_reader import BlockingIOReader

T = TypeVar('T')

class GDBInterface:
    def __init__(self, exe: str, gdb_line_cache: int = 256, inferior_line_cache: int = 128):
        self.process: Popen[bytes] = Popen(['gdb', '-i=mi3', exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if self.process.stdout is None or self.process.stderr is None or self.process.stdin is None:
            raise Exception("Failed to create pipes for GDB process")
        
        self.num_records = 0
        self.gdb_reader = BlockingIOReader(self.process.stdout, capacity=gdb_line_cache, _convert_output=self._convert_reader_output)
        
        self._last_result: dict[type[GDBRecord], GDBRecord] = dict()
        self._callbacks: dict[type[GDBRecord], list[Callable[[GDBRecord], None]]] = dict()
        self._after_finish_callbacks: dict[str, Callable[[GDBExecRecord], None]] = dict()
        self._breakpoints: dict[str, Breakpoint] = dict()
        
        self._running = False
                
        self.read_until(lambda r: isinstance(r, GDBSequenceEnd), block=True)  # Clear initial output
        # self._setup_inferior(inferior_line_cache)
        
        self.register_on_record(GDBExecRecord, self._builtin_callback)
        
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
        
    @property
    def inferior(self):
        return self._inferior_reader
    
    def register_on_record(self, line_type: type[GDBRecord_T], callback: Callable[[GDBRecord_T], None]):
        if not line_type in self._callbacks:
            self._callbacks[line_type] = []
        self._callbacks[line_type].append(cast(Callable[[GDBRecord], None], callback))
        
    def get_breakpoint(self, id: str) -> Breakpoint | None:
        return self._breakpoints.get(id)
    
    def _builtin_callback(self, record: GDBExecRecord):
        if record.reason is None:
            return
        elif 'exited' in record.reason:
            self._running = False
            
    def _convert_reader_output(self, src: IO[bytes], line: bytes):
        if line.strip()==b'':
            return None
        else:
            res = to_record(src, line, self.num_records, GDBInferiorRecord)
            self.num_records += 1
            return res

    def get_last_result(self, record_type: type[GDBRecord_T], default: T = None) -> GDBRecord_T | T:
        res = self._last_result.get(record_type, default)
        return cast(GDBRecord_T | T, res)
    
    def add_last_result(self, record: GDBRecord):
        self._last_result[type(record)] = record

    def _setup_inferior(self, saved_lines: int) -> None:
        self.exe_master_fd, exe_slave_fd = pty.openpty()
        tty_name = os.ttyname(exe_slave_fd)
        self.execute(f'tty {tty_name}')
        self.exe_in_fd, exe_out_fd, self.exe_slave_fd = open(self.exe_master_fd, 'wb'), open(self.exe_master_fd, 'rb'), open(exe_slave_fd, 'rb')
        self._inferior_reader = BlockingIOReader(exe_out_fd, capacity=saved_lines)
        
    def read_line(self, block: bool = False) -> GDBRecord | None:
        line = self.gdb_reader.read_line(block)
        
        if line:
            self.add_last_result(line)
            if isinstance(line, GDBExecRecord) and line.reason is not None and 'exited' in line.reason:
                self._running = False
            cbs = self._callbacks.get(type(line), [])
            for cb in cbs:
                cb(line)
        
        return line
    
    def execute(self, command: str | bytes, block: bool = True, force: bool = False) -> GDBCmdOutput | None:
        forced = False
        with self.gdb_reader.output as output:
            last_pushed = output.rget(0)
            if not last_pushed or not isinstance(last_pushed, GDBSequenceEnd):
                if force:
                    self.interrupt()
                    forced = True
                else:
                    raise Exception("GDB is not accepting input")
            
            output.clear()
        
        if forced: self.wait_until(lambda r: isinstance(r, GDBSequenceEnd))        
        self.write_line(command)
        if block:
            return GDBCmdOutput(self.read_until(lambda l: isinstance(l, GDBSequenceEnd), True)[0])
                
        return None
    
    def read_lines(self, block: bool = False) -> Generator[GDBRecord, None, None]:
        line = self.read_line(block)
        while line:
            yield line
            line = self.read_line(block)
        
        return None
    
    def read_until(self, predicate: Callable[[GDBRecord], bool], block: bool = False) -> tuple[list[GDBRecord], bool]:
        """Read records from GDB until a predicate is met or there are no more lines to read

        Args:
            predicate (Callable[[GDBRecord], bool]): Predicate to return on
            block (bool, optional): _description_. Whether this function should wait for new lines or immediately exit if no more lines

        Returns:
            tuple[list[GDBRecord], bool]: (list of records up until this function exits, whether the predicate was met)
        """
        lines = []
        for line in self.read_lines(block):
            lines.append(line)
            if predicate(line):
                return lines, True
        
        return lines, False
    
    def wait_until(self, predicate: Callable[[GDBRecord], bool]) -> GDBRecord | None:
        """Read records from GDB until a predicate is met, or GDB is closed

        Args:
            predicate (Callable[[GDBRecord], bool]): Predicate to return on

        Returns:
            GDBRecord | None: First record that satisfied the predicate, or None if GDB was closed
        """
        for line in self.read_lines(True):
            if predicate(line):
                return line
        
        return None
            
    def write_line(self, line: str | bytes, to_inferior: bool = False) -> None:
        fd = self.exe_in_fd if to_inferior else self.process.stdin
        if fd:
            if isinstance(line, str):
                fd.write(f'{line}\n'.encode())
            else:
                fd.write(line)
            fd.flush()
    
    def _verify_cmd_output(self, output: None | GDBCmdOutput) -> GDBCmdOutput:
        if output is None:
            raise Exception("Failed to run command: No output\n", output)
        return output
    
    def run(self, *args: str | int | float) -> GDBCmdOutput:
        output = self._verify_cmd_output(self.execute(f"-exec-arguments {' '.join(str(arg) for arg in args)}"))
        output = self._verify_cmd_output(self.execute(f'-exec-run'))
        
        if output.result.result != GDBResultType.RUNNING:
            raise Exception(f"Failed to run program: {output.result}")
        
        self._running = True
        return output
    
    def frame(self, level: int = 0):
        output = self._verify_cmd_output(self.execute(f'-stack-select-frame {level}'))
        output = self._verify_cmd_output(self.execute(f'-stack-info-frame'))
        
        return output
    
    def is_running(self):
        return self._running
    
    def breakpoint(self, loc: str) -> tuple[Breakpoint, GDBCmdOutput]:
        """Set a breakpoint in GDB

        Args:
            loc (str): _description_

        Returns:
            _type_: _description_
        """
        output = self._verify_cmd_output(self.execute(f'-break-insert {loc}'))
        bkpt_data: dict[str, Any] = output.result.data['bkpt']
        bp_id = cast(str, bkpt_data['number'])
        bp = Breakpoint(bkpt_data)
        self._breakpoints[bp_id] = bp
        return bp, output
    
    def continu(self):
        return self._verify_cmd_output(self.execute(f'-exec-continue'))
    
    def finish(self, block: bool = False):
        # if after is not None:
        #     self.execute('-stack-select-frame 1')
        #     res = self._verify_cmd_output(self.execute('-stack-info-frame'))
        #     self._after_finish_callbacks[res.result.data['frame']['addr']] = after
        output = self._verify_cmd_output(self.execute(f'-exec-finish'))
        if block:
            output.extend(self.read_until(lambda l: isinstance(l, GDBExecRecord) or isinstance(l, GDBSequenceEnd), True)[0])
        return output
    
    def break_finish(self, thread: str | None = None):
        frame = self.frame(1)
        return self.breakpoint(f"{f'-p {thread} ' if thread else ''}*{frame.result.data['frame']['addr']}")
    
    def delete_breakpoint(self, bp: Breakpoint | str):
        return self._verify_cmd_output(self.execute(f'-break-delete {bp.id if isinstance(bp, Breakpoint) else bp}'))
    
    def interrupt(self):
        self.process.send_signal(2)
    
    def close(self):
        self.process.kill()
        self.gdb_reader.close()