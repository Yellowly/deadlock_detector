from typing import Generic, TypeVar


T = TypeVar('T')

class CircularQueue(Generic[T]):
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.size = 0
        self.queue: list[T | None] = [None] * capacity
        self.front = self.rear = 0

    def __getitem__(self, index: int) -> T:
        res = self.queue[(index + (-self.rear if index < 0 else self.front)) % self.capacity]
        if res is None or index >= self.size or index < -self.size:
            raise IndexError("Index out of bounds")
        return res

    def push(self, item: T) -> None: 
        self.queue[self.rear] = item 
        self.rear = (self.rear + 1) % self.capacity
        if self.rear == self.front:
            self.front = (self.front + 1) % self.capacity
            
        self.size = min(self.size + 1, self.capacity)

    def pop(self) -> T | None:
        if self.size == 0:
            return None
        
        item = self.queue[self.front]

        if self.front != self.rear:
            self.front = (self.front + 1) % self.capacity
            
        self.size = max(self.size - 1, 0)
        return item

    def __len__(self) -> int:
        return self.size
    
    def rget(self, index: int) -> T | None:
        """Get element relative to back of the queue, regardless of whether it has been popped or not

        Args:
            index (int): Index of the element after the back of the queue, where 0 is the most recently pushed element, 1 is the one before that, etc.

        Returns:
            T | None: The element at the specified index relative to the back of the queue, or None if the element was never set.
        """
        if index >= self.capacity:
            raise IndexError("Index out of bounds")
        return self.queue[(self.rear - 1 - index) % self.capacity]
    
    def lget(self, index: int) -> T | None:
        """Get element relative to front of the queue, regardless of whether it has been popped or not

        Args:
            index (int): Index of the element before the front of the queue, where 0 is the most recently popped element, 1 is the one before that, etc.

        Returns:
            T | None: The element at the specified index relative to the front of the queue, or None if the element is past the end of the queue or the element was never set.
        """
        if index >= self.capacity:
            raise IndexError("Index out of bounds")
        i = (self.front - 1 - index) % self.capacity
        return None if i < self.rear else self.queue[i]
    
    def clear(self) -> None:
        self.front = self.rear
        self.size = 0