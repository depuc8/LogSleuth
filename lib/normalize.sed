# normalize.sed - reduce a log message to its signature.
#
# Two messages that differ only in ids, addresses, counters or timings should
# produce the same signature, so that "5000 timeouts" can be counted as one
# problem rather than 5000. Run with: sed -E -f normalize.sed
#
# Rule order matters: the more specific patterns (UUIDs, IPs) have to run
# before the generic number collapse at the bottom.

# UUIDs
s/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<UUID>/g

# embedded timestamps (2026-07-21 02:18:04, 2026-07-21T02:18:04.123Z)
s/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.,][0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?/<TS>/g

# MAC addresses
s/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/<MAC>/g

# IPv4 with optional port, then IPv6
s/[0-9]{1,3}(\.[0-9]{1,3}){3}(:[0-9]+)?/<IP>/g
s/([0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}/<IP>/g

# hex literals and long hex strings (hashes, request ids, addresses)
s/0[xX][0-9a-fA-F]+/<HEX>/g
s/[0-9a-fA-F]{12,}/<HEX>/g

# user names in auth messages
s/(Failed password for |Accepted [a-z]+ for |Invalid user |Connection closed by (invalid |authenticating )?user )(invalid user )?[^ ]+/\1<USER>/

# every remaining number, then fold "<N>.<N>" decimals into one placeholder
s/[0-9]+/<N>/g
s/<N>\.<N>/<N>/g

# tidy: collapse whitespace, trim, cap the length
s/[[:space:]]+/ /g
s/^ //
s/ $//
s/^(.{200}).*$/\1/
