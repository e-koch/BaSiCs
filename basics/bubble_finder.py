
import numpy as np
import astropy.units as u

from bubble_segment2D import BubbleSegment


class BubbleFinder(object):
    """docstring for BubbleFinder"""
    def __init__(self, arg):
        super(BubbleFinder, self).__init__()
        self.arg = arg

