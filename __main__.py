from detector import DeadlockDetector
from gdb_interface.records import GDBInferiorRecord, GDBSequenceEnd

if __name__ == '__main__':
    import argparse
    import traceback
    
    parser = argparse.ArgumentParser(prog='deadlock_detector', 
                                     description='Script for detecting deadlocks in C/C++ programs that use POSIX mutexes for synchronization', 
                                     epilog='See [insert github repo here] for more info')
    parser.add_argument('executable_path', type=str)
    parser.add_argument('-p', '--print', action='store_true')
    parser.add_argument('executable_args', nargs='*')
    args = parser.parse_args()
        
    with DeadlockDetector(args.executable_path) as dd:
        if args.print:
            dd.gdb.register_on_record(GDBInferiorRecord, lambda r: print(r))
        
        try:
            dd.run(*args.executable_args)
        except KeyboardInterrupt:
            print(traceback.format_exc())
            print(dd.gdb.execute('thread apply all bt 8', force=True))
            print(dd.program.disp_locks())
        except Exception as e:
            print("ERROR:", e)
            print(traceback.format_exc())
            print(dd.gdb.execute('thread apply all bt 8', force=True))
            print(dd.program.disp_locks())
        
        dd.verify_lock_stacks()