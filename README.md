# Deadlock Detector
Basic python script for helping to check for deadlocks in C/C++ programs that use POSIX mutexes as the primary synchronization mechanism.

# Usage
## CLI
This script can be called from the command line:
```bash
python3 deadlock_detector [--print] [executable file] [executable arguments]
```

## Import
Use a context manager to open up a DeadlockDetector:
```python
with DeadlockDetector({ executable file name }) as dd:
  dd.run({ executable arguments }) # Run the program using the deadlock detector
  dd.verify_lock_stacks() # Ensure all pthread_mutex_lock call stacks don't have deadlock
```
