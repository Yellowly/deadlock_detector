from __future__ import annotations

from enum import Enum
import traceback
from typing import Callable, cast

from gdb_interface import GDBInterface
from gdb_interface.records import GDBExecRecord, GDBNotifyRecord, GDBProgramState, GDBSequenceEnd


class ProgramState:
    def __init__(self):
        self._thread_data: dict[str, ThreadData] = dict()
        self._active_threads: set[ThreadData] = set()
        self._mutex_refs: dict[str, MutexRef] = dict()
    
    def get_mutex(self, mutex_addr: str) -> MutexRef:
        res = self._mutex_refs.get(mutex_addr)
        if res is None:
            res = MutexRef(mutex_addr)
            self._mutex_refs[mutex_addr] = res
        return res        
    
    def get_thread(self, thread_id: str) -> ThreadData:
        res = self._thread_data.get(thread_id)
        if res is None:
            res = ThreadData(self, thread_id)
            self._thread_data[thread_id] = res
            self._active_threads.add(res)
        return res
    
    def disp_locks(self) -> str:
        res = []
        for thread in self._thread_data.values():
            res.append(f"Thread {thread.thread_id} : {thread.active_locks}")
        return '\n'.join(res)
    

class MutexState(Enum):
    locked = "locked"
    unlocked = "unlocked"
    
class LockCallInfo:
    def __init__(self, addr: str, state: MutexState):
        self.addr = addr
        self.state = state
        
class LockStack:
    def __init__(self, active_threads: set[ThreadData]):
        self.parallel_threads = active_threads
        self.locks: list[MutexRef] = []
    
    def copy(self):
        res = LockStack(self.parallel_threads)
        res.locks = self.locks.copy()
        return res
    
    def add(self, mutex: MutexRef):
        self.locks.append(mutex)
        
    def remove(self, mutex: MutexRef):
        self.locks.remove(mutex)
        
    def __contains__(self, mutex: MutexRef):
        return mutex in self.locks
    
    def __len__(self):
        return len(self.locks)
    
    def lowest_shared_mutex(self, other: LockStack):
        # probably a better way to do this
        for l1 in self.locks:
            for l2 in other.locks:
                if l1 == l2:
                    return l1
        return None
    
    def valid_with(self, other: LockStack) -> bool:
        """Check if two stacks of mutex locks can occur without deadlock

        Args:
            other (LockStack): Stack of locks to compare to

        Returns:
            bool: Whether or not the two stacks of mutex locks can be multithreaded without deadlock
        """
        return self.lowest_shared_mutex(other) == other.lowest_shared_mutex(self)
                    
    def is_substack(self, other: LockStack):
        min_idx = 0
        try:
            for l in self.locks:
                min_idx = other.locks.index(l, min_idx) + 1
            return True
        except:
            return False
        
    def __str__(self):
        return f"[{', '.join(str(m) for m in self.locks)}]"
            
                
        
    
class MutexRef:
    def __init__(self, addr: str):
        self.addr = addr
        self.locked_by: ThreadData | None = None
    
    @property
    def state(self):
        return MutexState.unlocked if self.locked_by is None else MutexState.locked
    
    def __str__(self):
        return self.addr
        
class ThreadData:
    def __init__(self, program: ProgramState, thread_id: str):
        self._program = program
        self.thread_id = thread_id
        self.mutex_calls: list[LockCallInfo] = []
        self.active_locks: LockStack = LockStack(self._program._active_threads)
        self.lock_stacks: list[LockStack] = []
        self.pending_lock: MutexRef | None = None
        self.started: bool = False
        self.ignore_locks: bool = False
        
    def add_pending_lock(self, mutex: MutexRef):
        # print("Pending...", self.thread_id, str(mutex))
        if not self.started: return
        if self.pending_lock is not None:
            raise Exception(f'Thread {self.thread_id} cannot attempt to lock two mutexes at the same time', self.pending_lock, mutex)
        self.pending_lock = mutex
        if mutex.locked_by and mutex.locked_by.pending_lock and mutex.locked_by.pending_lock in self.active_locks:
            raise Exception("Circular wait detected", self.thread_id, mutex.locked_by.thread_id)
    
    def has_pending_lock(self):
        return self.pending_lock is not None
    
    def aquire_lock(self):
        """Signify that this thread aquires the mutex it is currently attempting to aquire

        Raises:
            Exception: _description_
        """
        if not self.started: return
        # print("Aquired!!", self.thread_id, str(self.pending_lock))
        if self.pending_lock is None:
            raise Exception(f'Thread {self.thread_id} does not have a pending mutex', self.pending_lock)
        if self.pending_lock.locked_by is not None:
            raise Exception(f'Thread {self.thread_id} attempted to aquire a mutex that is already locked', str(self.pending_lock), self.pending_lock.locked_by.thread_id)
        self.pending_lock.locked_by = self
        self.active_locks.add(self.pending_lock)
        self.mutex_calls.append(LockCallInfo(self.pending_lock.addr, MutexState.locked))
        self.pending_lock = None
        
    def unlock_mutex(self, mutex: MutexRef):
        """Signify that this tread unlocks a mutex it currently holds

        Args:
            mutex (MutexRef): mutex to unlock

        Raises:
            Exception: If this thread does not own the mutex
        """
        if not self.started: return
        # print("Released!", self.thread_id, str(mutex))
        if mutex.locked_by != self:
            raise Exception(f"Thread {self.thread_id} attempted to unlock a mutex it has not locked", str(mutex), mutex.locked_by)
        
        mutex.locked_by = None
        self.mutex_calls.append(LockCallInfo(mutex.addr, MutexState.unlocked))
        
        if not any(self.active_locks.is_substack(stack) and 
               (self.active_locks.parallel_threads.issuperset(stack.parallel_threads) and 
                len(self.active_locks.parallel_threads) > len(stack.parallel_threads)) for stack in self.lock_stacks):
            self.lock_stacks.append(self.active_locks)
            self.active_locks = self.active_locks.copy()
            
        self.active_locks.remove(mutex)
    
    def opens_thread(self, new_thread: ThreadData):
        self._program._active_threads.add(new_thread)
    
    def handle_exit(self):
        self._program._active_threads = self._program._active_threads.copy()
        self._program._active_threads.remove(self)
        if len(self.active_locks) > 0:
            raise Exception(f'Thread {self.thread_id} was closed while still holding a lock')
        
class DeadlockDetector:
    def __init__(self, exe: str) -> None:
        self.program = ProgramState()
        self.thread_data: dict[str, ThreadData] = dict()
        self.active_threads: set[str] = set()
        
        self.gdb = GDBInterface(exe)
        
        self._breakpoint_funcs: dict[str, Callable[[GDBExecRecord], None]] = dict()
        
        self.mutex_lock_bp = self.gdb.breakpoint('pthread_mutex_lock')[0].id
        self._breakpoint_funcs[self.mutex_lock_bp] = self._on_mutex_lock
        self._breakpoint_funcs[self.gdb.breakpoint('pthread_mutex_unlock')[0].id] = self._on_mutex_unlock
        self._breakpoint_funcs[self.gdb.breakpoint('pthread_create')[0].id] = self._on_pthread_create
        self.cond_wait_bp = self.gdb.breakpoint('pthread_cond_wait')[0].id
        self._breakpoint_funcs[self.cond_wait_bp] = self._on_pthread_cond_wait
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def _on_mutex_lock(self, record: GDBExecRecord):
        thread, mutex = self.lock_frame_to_data(record)
        # print("locking",thread.thread_id, mutex, thread.started, thread.ignore_locks)
        thread.add_pending_lock(mutex)
        
    def _on_mutex_unlock(self, record: GDBExecRecord):
        thread, mutex = self.lock_frame_to_data(record)
        # print("unlocking",thread.thread_id, mutex)
        thread.unlock_mutex(mutex)
        
    def _on_pthread_create(self, record: GDBExecRecord):
        thread = self.program.get_thread(record.data['thread-id'])
        thread.ignore_locks = True
        # thread.unlock_mutex(mutex)
        
    def _on_pthread_cond_wait(self, record: GDBExecRecord):
        thread = self.program.get_thread(record.data['thread-id'])
        # print(record, '\n', record.data['frame']['args'][1].values())
        mutex = self.program.get_mutex(record.data['frame']['args'][1]['value'])
        thread.unlock_mutex(mutex)
        thread.add_pending_lock(mutex)
        
    def _after_pthread_create(self, record: GDBExecRecord):
        thread = self.program.get_thread(record.data['thread-id'])
        thread.started = True
    
    def lock_frame_to_data(self, record: GDBExecRecord) -> tuple[ThreadData, MutexRef]:
        return self.program.get_thread(record.data['thread-id']), self.program.get_mutex(record.data['frame']['args'][0]['value'])
    
    def run(self, *args: str | int | float):
        temp_bps: dict[str, str] = dict()
        self.gdb.run(*args)
        while self.gdb.is_running():
            for record in self.gdb.read_lines():
                # print(record)
                if isinstance(record, GDBSequenceEnd):
                    last_exec = self.gdb.get_last_result(GDBExecRecord)
                    if last_exec is None or last_exec.state != GDBProgramState.STOPPED:
                        continue
                    if last_exec.reason=="breakpoint-hit":
                        thread = self.program.get_thread(last_exec.data['thread-id'])
                        bp = last_exec.data['bkptno']
                        func = self._breakpoint_funcs.get(bp)
                        if func: 
                            func(last_exec)
                            if func in [self._on_mutex_lock, self._on_pthread_create, self._on_pthread_cond_wait]:
                                new_bp = self.gdb.break_finish(thread.thread_id)[0].id
                                temp_bps[new_bp] = bp
                                # print("created bp", new_bp)
                        else:
                            if bp in temp_bps:
                                from_bp = temp_bps.pop(bp)
                                # print("removing", bp)
                                thread.ignore_locks = False
                                if from_bp in [self.mutex_lock_bp, self.cond_wait_bp]:
                                    thread.aquire_lock()
                                
                                self.gdb.delete_breakpoint(bp)
                                # print(, 'deleting', bp)
                    
                    elif last_exec.reason and 'exited' in last_exec.reason:
                        # print("breaking")
                        break
                    
                    self.gdb.continu()
                    
                elif isinstance(record, GDBNotifyRecord):
                    if record.notif_type=='thread-created':
                        # print("created thread", record.data['id'])
                        self.program.get_thread(record.data['id']).started = True
                    elif record.notif_type=='thread-exited':
                        self.program.get_thread(record.data['id']).handle_exit()
        
        # print("DONE!")
        # print(self.program.get_thread('2').mutex_calls)
        
    
    def verify_lock_stacks(self):
        threads = list(self.program._thread_data.values())
        while len(threads) > 0:
            comparing = threads.pop()
            for stack in comparing.lock_stacks:
                # print(stack, len(stack.parallel_threads))
                for thread in threads:
                    if thread in stack.parallel_threads and any(not stack.valid_with(other) for other in thread.lock_stacks):
                        raise Exception("Deadlock detected", comparing, thread)
        return True
    
    def on_thread_notif(self, record: GDBNotifyRecord):
        if record.notif_type == 'thread-created':
            print("created thread", record.data['id'])
            thread_id = cast(str, record.data['id'])
            self.active_threads.add(thread_id)
        elif record.notif_type == 'thread-exited':
            thread_id = cast(str, record.data['id'])
            self.active_threads.remove(thread_id)
            
    def on_stopped(self, record: GDBExecRecord):
        if record.state==GDBProgramState.STOPPED:
            if record.reason=='breakpoint-hit':
                bp_id = cast(str, record.data['bkptno'])
                        
    def close(self):
        self.gdb.close()

if __name__=='__main__':
    print("Starting GDB Interface Test")
    # gdb = GDBInterface('bank_app')
    # print(gdb.run('1 1 5 ledger.txt'))

    # for line in itertools.takewhile(lambda r: not isinstance(r, GDBSequenceEnd), gdb.read_lines(block=True)):
    #     print(line)
    #     if isinstance(line, GDBDictRecord) and line.result_name==b'thread-exited':
    #         print(line.data.get('id'))

    with DeadlockDetector('bank_app') as dd:
        try:
            dd.run('3 3 10 ledger.txt')
            print("SUCCESS")
        except Exception as e:
            print("ERROR:", e)
            print(traceback.format_exc())
            # sleep(1.0)
            print(dd.gdb.execute('thread apply all bt 8'))
            print(dd.program.disp_locks())
        dd.verify_lock_stacks()
            

    # dict(item.split('=') for item in data_string.split(','))