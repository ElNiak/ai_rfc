{::comment}
ai_rfc:struct:conn begin
{:/comment}
**Connection lifecycle**

~~~
+------+
| idle |
+------+
+------+
| open |
+------+
+--------+
| closed |
+--------+
idle --connect--> open
open --timeout--> closed
~~~

| From | Event | Guard | To | Claim |
|---|---|---|---|---|
| idle | connect | - | open | `ai_rfc:spec:5.1` |
| open | timeout | no traffic | closed | `ai_rfc:spec:5.2` |
{::comment}
ai_rfc:struct:conn end
{:/comment}
