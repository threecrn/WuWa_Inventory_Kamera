from math import gcd

def reduce_ratio(width: int | float, height: int | float) -> tuple[int, int]:
    """Reduce width and height to their simplest ratio."""
    divisor = gcd(int(width), int(height))
    return (int(width) // divisor, int(height) // divisor)
