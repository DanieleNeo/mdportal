---
id: CAT-0001
service: catalog
status: fatta
version: '1.0'
tags: [tariffe]
---
# Regole tariffarie

```mermaid
flowchart TD
    A[Richiesta] --> B{Titolo valido?}
    B -->|Sì| C[Accettazione]
    B -->|No| D[Rifiuto]
```
