# A cited figure, worked

The lint's citation window starts counting at the line right after the
closing fence, so the `{: ...}` attribute line is the first of the three;
the example below lands its caption and citations on the third, well
inside it.

```
~~~
+--------+   raw data   +--------+   evidence   +---------+
| Client | -----------> | Server | -----------> | Storage |
+--------+              +--------+              +---------+
~~~
{: #fig-overview title="Components of the system"}

Clients submit raw data that the server stores as evidence. `ai_rfc:mark:arch.1` `ai_rfc:mark:store.2`
```
