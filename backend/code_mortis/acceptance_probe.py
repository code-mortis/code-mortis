"""Temporary synthetic inputs for PR #16 review-publication acceptance testing."""


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit: 0 maps to 32 and 100 maps to 212."""
    return celsius * 5 / 9 + 32


def is_even(number: int) -> bool:
    """Report whether an integer is divisible by two without a remainder."""
    return number % 2 == 1
