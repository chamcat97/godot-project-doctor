extends BaseHandler

# Attached to inherit.tscn's root. Inherits _on_inherited from BaseHandler;
# does NOT define _on_vanished anywhere in the chain -> that connection is
# flagged as BROKEN_SIGNAL_CONNECTION.
