{::comment}
ai_rfc:struct:header begin
{:/comment}
**Message header**

~~~
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|Version|  Type |                     Length                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Payload (variable)                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
~~~

| Field | Bits | Description | Claim |
|---|---|---|---|
| Version | 4 | Protocol version. | `ai_rfc:spec:1.1` |
| Type | 4 | - | `ai_rfc:spec:1.2` |
| Length | 24 | - | `ai_rfc:spec:1.3` |
| Payload | variable | - | `ai_rfc:spec:1.4` |
{::comment}
ai_rfc:struct:header end
{:/comment}
