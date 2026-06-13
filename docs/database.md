# Database Schemas

Several Trust Stack libraries ship a durable backend. The canonical DDL for the
PostgreSQL / SQLite tables is shown below; the libraries also create these tables
automatically on first use.

## task-dedupe

```sql
--8<-- "docs/schemas/truststack-task-dedupe.sql"
```

## entity-canon

```sql
--8<-- "docs/schemas/truststack-entity-canon.sql"
```

## shipped-or-not

```sql
--8<-- "docs/schemas/truststack-shipped-or-not.sql"
```

## meta-token-vault

```sql
--8<-- "docs/schemas/truststack-meta-token-vault.sql"
```
