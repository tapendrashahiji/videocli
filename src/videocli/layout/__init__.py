"""Where things are on screen: text, faces, objects and free space over time.

The ``layout`` analyzer records it per stable window; placement (``gfx add --place auto``,
caption ``position=auto``, ``reframe --mode stack --face auto``) and the validators
(``gfx lint`` rules, ``verify``'s layout check) read it, so graphics land in the right place
without anyone looking at a screenshot for every decision.

Boxes are ``[x, y, w, h]`` fractions of the frame (top-left origin).
"""
