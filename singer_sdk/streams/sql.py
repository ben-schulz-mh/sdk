"""Base class for SQL-type streams."""

import abc
from typing import Any, Dict, Iterable, List, Optional, cast

import singer
import sqlalchemy

from singer_sdk import typing as th
from singer_sdk.exceptions import ConfigValidationError
from singer_sdk.helpers._singer import CatalogEntry, MetadataMapping
from singer_sdk.plugin_base import PluginBase as TapBaseClass
from singer_sdk.streams.core import Stream


class SQLConnector:
    """Base class for SQLAlchemy-based connectors.

    The connector class serves as a wrapper around the SQL connection.

    The functions of the connector are:

    - connecting to the source
    - generating SQLAlchemy connection and engine objects
    - discovering schema catalog entries
    - performing type conversions to/from JSONSchema types
    - dialect-specific functions, such as escaping and fully qualified names
    """

    def __init__(
        self, config: Optional[dict] = None, sqlalchemy_url: Optional[str] = None
    ) -> None:
        """Initialize the SQL connector.

        Args:
            config: The parent tap or target object's config.
            sqlalchemy_url: Optional URL for the connection.
        """
        self._config: Dict[str, Any] = config or {}
        self._sqlalchemy_url: Optional[str] = sqlalchemy_url or None
        self._connection: Optional[sqlalchemy.engine.Connection] = None

    @property
    def config(self) -> dict:
        """If set, provides access to the tap or target config.

        Returns:
            The settings as a dict.
        """
        return self._config

    def create_sqlalchemy_connection(self) -> sqlalchemy.engine.Connection:
        """Return a new SQLAlchemy connection using the provided config.

        Returns:
            A newly created SQLAlchemy engine object.
        """
        return (
            self.create_sqlalchemy_engine()
            .connect()
            .execution_options(stream_results=True)
        )

    def create_sqlalchemy_engine(self) -> sqlalchemy.engine.Engine:
        """Return a new SQLAlchemy engine using the provided config.

        Developers can generally override just one of the following:
        `sqlalchemy_engine`, sqlalchemy_url`.

        Returns:
            A newly created SQLAlchemy engine object.
        """
        return sqlalchemy.create_engine(self.sqlalchemy_url)

    @property
    def connection(self) -> sqlalchemy.engine.Connection:
        """Return or set the SQLAlchemy connection object.

        Returns:
            The active SQLAlchemy connection object.
        """
        if not self._connection:
            self._connection = self.create_sqlalchemy_connection()

        return self._connection

    @property
    def sqlalchemy_url(self) -> str:
        """Return the SQLAlchemy URL string.

        Returns:
            The URL as a string.
        """
        if not self._sqlalchemy_url:
            self._sqlalchemy_url = self.get_sqlalchemy_url(self.config)

        return self._sqlalchemy_url

    def get_sqlalchemy_url(self, config: Dict[str, Any]) -> str:
        """Return the SQLAlchemy URL string.

        Developers can generally override just one of the following:
        `sqlalchemy_engine`, `get_sqlalchemy_url`.

        Args:
            config: A dictionary of settings from the tap or target config.

        Returns:
            The URL as a string.

        Raises:
            ConfigValidationError: If no valid sqlalchemy_url can be found.
        """
        if "sqlalchemy_url" not in config:
            raise ConfigValidationError(
                "Could not find or create 'sqlalchemy_url' for connection."
            )

        return cast(str, config["sqlalchemy_url"])

    def to_jsonschema_type(cls, sql_type: sqlalchemy.types.TypeEngine) -> dict:
        """Return a JSON Schema representation of the provided type.

        By default will call `typing.to_jsonschema_type()` for strings and Python types.

        Developers may override this method to accept additional input argument types,
        to support non-standard types, or to provide custom typing logic.

        Args:
            sql_type (Union[type, str, Any]): The string representation of the SQL type,
                or a Python class, or a custom-specified object.

        Raises:
            ValueError: If the type received could not be translated to jsonschema.

        Returns:
            The JSON Schema representation of the provided type.
        """
        if isinstance(sql_type, (type, str)):
            return th.to_jsonschema_type(sql_type)

        raise ValueError(f"Unexpected type received: '{type(sql_type).__name__}'")

    def to_sql_type(cls, jsonschema_type: dict) -> sqlalchemy.types.TypeEngine:
        """Return a JSON Schema representation of the provided type.

        By default will call `typing.to_jsonschema_type()` for strings and Python types.

        Developers may override this method to accept additional input argument types,
        to support non-standard types, or to provide custom typing logic.

        Args:
            jsonschema_type: The JSON Schema representation of the source type.

        Raises:
            ValueError: If the type received could not be translated to a SQL type.

        Return:
            The SQLAlchemy type representation of the data type.
        """
        # TODO: Add mapping logic

        raise ValueError(f"Unexpected type received: '{jsonschema_type}'")

    @staticmethod
    def get_fully_qualified_name(
        table_name: str,
        schema_name: Optional[str] = None,
        db_name: Optional[str] = None,
        delimiter: str = ".",
    ) -> str:
        """Concatenates a fully qualified name from the parts.

        Args:
            table_name: The name of the table.
            schema_name: The name of the schema. Defaults to None.
            db_name: The name of the database. Defaults to None.
            delimiter: Generally: '.' for SQL names and '-' for Singer names.

        Raises:
            ValueError: If neither schema_name or db_name are provided.

        Returns:
            The fully qualified name as a string.
        """
        if db_name and schema_name:
            result = delimiter.join([db_name, schema_name, table_name])
        elif db_name:
            result = delimiter.join([db_name, table_name])
        elif schema_name:
            result = delimiter.join([schema_name, table_name])
        else:
            raise ValueError(
                "Schema name or database name was expected for stream: "
                + ":".join(
                    [
                        db_name or "(unknown-db)",
                        schema_name or "(unknown-schema)",
                        table_name,
                    ]
                )
            )

        return result

    def discover_catalog_entries(self) -> List[dict]:
        """Return a list of catalog entries from discovery.

        Returns:
            The discovered catalog entries as a list.
        """
        result: List[dict] = []
        engine = self.create_sqlalchemy_engine()
        inspected = sqlalchemy.inspect(engine)
        for schema_name in inspected.get_schema_names():
            table_names = inspected.get_table_names(schema=schema_name)
            try:
                view_names = inspected.get_view_names(schema=schema_name)
            except NotImplementedError:
                # TODO: Handle `get_view_names()`` not implemented
                # self.logger.warning(
                #     "Provider does not support get_view_names(). "
                #     "Streams list may be incomplete or `is_view` may be unpopulated."
                # )
                view_names = []
            object_names = [(t, False) for t in table_names] + [
                (v, True) for v in view_names
            ]
            for table_name, is_view in object_names:
                # table_obj: sqlalchemy.Table = sqlalchemy.Table(
                #     table_name, schema=schema_name
                # )
                # inspected.reflect_table(table=table_obj)
                possible_primary_keys: List[List[str]] = []

                pk_def = inspected.get_pk_constraint(table_name, schema=schema_name)
                if pk_def:
                    possible_primary_keys.append(pk_def)

                for index_def in inspected.get_indexes(table_name, schema=schema_name):
                    if index_def.get("unique", False):
                        possible_primary_keys.append(index_def["column_names"])

                table_schema = th.PropertiesList()
                for column_def in inspected.get_columns(table_name, schema=schema_name):
                    column_name = column_def["name"]
                    is_nullable = column_def.get("nullable", False)
                    jsonschema_type: dict = self.to_jsonschema_type(
                        cast(
                            sqlalchemy.types.TypeEngine, column_def["type"]
                        ).python_type
                    )
                    table_schema.append(
                        th.Property(
                            name=column_name,
                            wrapped=th.CustomType(jsonschema_type),
                            required=not is_nullable,
                        )
                    )

                schema = table_schema.to_dict()
                addl_replication_methods: List[str] = []
                key_properties = next(iter(possible_primary_keys), None)
                replication_method = next(
                    reversed(["FULL_TABLE"] + addl_replication_methods)
                )
                unique_stream_id = self.get_fully_qualified_name(
                    db_name=None,
                    schema_name=schema_name,
                    table_name=table_name,
                    delimiter="-",
                )
                catalog_entry = CatalogEntry(
                    tap_stream_id=unique_stream_id,
                    stream=unique_stream_id,
                    table=table_name,
                    key_properties=key_properties,
                    schema=singer.Schema.from_dict(schema),
                    is_view=is_view,
                    replication_method=replication_method,
                    metadata=MetadataMapping.get_standard_metadata(
                        schema_name=schema_name,
                        schema=schema,
                        replication_method=replication_method,
                        key_properties=key_properties,
                        valid_replication_keys=None,  # Must be defined by user
                    ),
                    database=None,  # Expects single-database context
                    row_count=None,
                    stream_alias=None,
                    replication_key=None,  # Must be defined by user
                )
                result.append(catalog_entry.to_dict())

        return result

    def table_exists(self, full_table_name: Optional[str] = None) -> Optional[bool]:
        """Determine if the target table already exists.

        Args:
            full_table_name: the target table name.

        Returns:
            True if table exists, False if not, None if unsure or undetectable.
        """
        return None

    def column_exists(
        self,
        full_table_name: Optional[str] = None,
        column_name: Optional[str] = None,
    ) -> Optional[bool]:
        """Determine if the target table already exists.

        Args:
            full_table_name: the target table name.
            column_name: the target column name.

        Returns:
            True if table exists, False if not, None if unsure or undetectable.
        """
        return None

    def create_empty_table(
        self,
        full_table_name: str,
        schema: dict,
        primary_keys: Optional[List[str]] = None,
        partition_keys: Optional[List[str]] = None,
        as_temp_table: bool = False,
    ) -> None:
        """Create an empty target table.

        Args:
            full_table_name: the target table name.
            schema: the JSON schema for the new table.
            primary_keys: list of key properties.
            partition_keys: list of partition keys.
            as_temp_table: True to create a temp table.
        """
        pass

    def _create_empty_column(
        self,
        full_table_name: str,
        column_name: str,
        sql_type: sqlalchemy.types.TypeEngine,
    ) -> None:
        """Create a new column.

        Args:
            full_table_name: The target table name.
            column_name: The name of the new column.
            sql_type: SQLAlchemy type engine to be used in creating the new column.
        """

    def prepare_table(
        self,
        full_table_name: str,
        schema: dict,
        primary_keys: List[str],
        partition_keys: Optional[List[str]] = None,
        as_temp_table: bool = False,
    ) -> None:
        """Adapt target table to provided schema if possible.

        Args:
            full_table_name: the target table name.
            schema: the JSON Schema for the table.
            primary_keys: list of key properties.
            partition_keys: list of partition keys.
            as_temp_table: True to create a temp table.
        """
        if not self.table_exists(full_table_name=full_table_name):
            self.create_empty_table(
                full_table_name=full_table_name,
                schema=schema,
                primary_keys=primary_keys,
                partition_keys=partition_keys,
                as_temp_table=as_temp_table,
            )
            return

        for property_name, property_def in schema["properties"].items():
            self.prepare_column(full_table_name, property_name, property_def)

    def prepare_column(
        self,
        full_table_name: str,
        column_name: str,
        sql_type: sqlalchemy.types.TypeEngine,
    ) -> None:
        """Adapt target table to provided schema if possible.

        Args:
            full_table_name: the target table name.
            column_name: the target column name.
            sql_type: the SQLAlchemy type.
        """
        if not self.column_exists(full_table_name, column_name):
            self._create_empty_column(
                full_table_name=full_table_name,
                column_name=column_name,
                sql_type=sql_type,
            )
            return

        self._adapt_column_type(
            full_table_name,
            column_name=column_name,
            sql_type=sql_type,
        )

    def merge_sql_types(
        self, sql_types: List[sqlalchemy.types.TypeEngine]
    ) -> sqlalchemy.types.TypeEngine:
        """Return a compatible SQL type for the selected type list.

        Args:
            sql_types: List of SQL types.

        Returns:
            A SQL type that is compatible with the input types.

        Raises:
            ValueError: If sql_types argument has zero members.
        """
        if not sql_types:
            raise ValueError("Expected at least one member in `sql_types` argument.")

        if len(sql_types) == 1:
            return sql_types[0]

        sql_types = self._sort_types(sql_types)

        if len(sql_types) >= 2:
            return self.merge_sql_types(
                [self.merge_sql_types([sql_types[0], sql_types[1]])] + sql_types[2:]
            )

        assert len(sql_types) == 2
        if isinstance(
            sql_types[0].as_generic(),
            (sqlalchemy.types.String, sqlalchemy.types.Unicode),
        ):
            return sql_types[0]

    def _sort_types(
        self, sql_types: List[sqlalchemy.types.TypeEngine]
    ) -> List[sqlalchemy.types.TypeEngine]:
        """Return the input types sorted from most to least compatible.

        For example, [Smallint, Integer, Datetime, String, Double] would become
        [Unicode, String, Double, Integer, Smallint, Datetime].
        String types will be listed first, then decimal types, then integer types,
        then bool types, and finally datetime and date. Higher precision, scale, and
        length will be sorted earlier.

        Args:
            sql_types (List[sqlalchemy.types.TypeEngine]): [description]

        Returns:
            The sorted list.
        """
        return sql_types

    def _get_column_type(
        self, full_table_name: str, column_name: str
    ) -> sqlalchemy.types.TypeEngine:
        """Gets the SQL type of the declared column.

        Args:
            full_table_name: The name of the table.
            column_name: The name of the column.

        Return:
            The type of the column.
        """
        pass

    def _adapt_column_type(
        self,
        full_table_name: str,
        column_name: str,
        sql_type: sqlalchemy.types.TypeEngine,
    ) -> None:
        """Adapt table column type to support the new JSON schema type.

        Args:
            full_table_name: The target table name.
            column_name: The target column name.
            sql_type: The new SQLAlchemy type.
        """
        compatible_sql_type = self.merge_sql_types(
            [
                self._get_column_type(full_table_name, column_name),
                self.to_sql_type(sql_type),
            ]
        )
        self.connection.execute(
            f"ALTER TABLE {full_table_name} "
            f"ALTER COLUMN {column_name} ({compatible_sql_type})"
        )


class SQLStream(Stream, metaclass=abc.ABCMeta):
    """Base class for SQLAlchemy-based streams."""

    connector_class = SQLConnector

    def __init__(
        self,
        tap: TapBaseClass,
        catalog_entry: dict,
        connector: Optional[SQLConnector] = None,
    ) -> None:
        """Initialize the database stream.

        If `connector` is omitted, a new connector will be created.

        Args:
            tap: The parent tap object.
            catalog_entry: Catalog entry dict.
            connector: Optional connector to reuse.
        """
        self._connector: SQLConnector
        if connector:
            self._connector = connector
        else:
            self._connector = self.connector_class(dict(tap.config))

        self.catalog_entry = catalog_entry
        super().__init__(
            tap=tap,
            schema=self.schema,
            name=self.tap_stream_id,
        )

    @property
    def _singer_catalog_entry(self) -> CatalogEntry:
        """Return catalog entry as specified by the Singer catalog spec.

        Returns:
            A CatalogEntry object.
        """
        return cast(CatalogEntry, CatalogEntry.from_dict(self.catalog_entry))

    @property
    def connector(self) -> SQLConnector:
        """The connector object.

        Returns:
            The connector object.
        """
        return self._connector

    @property
    def metadata(self) -> MetadataMapping:
        """The Singer metadata.

        Metadata from an input catalog will override standard metadata.

        Returns:
            Metadata object as specified in the Singer spec.
        """
        return self._singer_catalog_entry.metadata

    @property
    def schema(self) -> dict:
        """Return metadata object (dict) as specified in the Singer spec.

        Metadata from an input catalog will override standard metadata.

        Returns:
            The schema object.
        """
        return cast(dict, self._singer_catalog_entry.schema.to_dict())

    @property
    def tap_stream_id(self) -> str:
        """Return the unique ID used by the tap to identify this stream.

        Generally, this is the same value as in `Stream.name`.

        In rare cases, such as for database types with multi-part names,
        this may be slightly different from `Stream.name`.

        Returns:
            The unique tap stream ID as a string.
        """
        return self._singer_catalog_entry.tap_stream_id

    @property
    def fully_qualified_name(self) -> str:
        """Generate the fully qualified version of the table name.

        Raises:
            ValueError: If table_name is not able to be detected.

        Returns:
            The fully qualified name.
        """
        catalog_entry = self._singer_catalog_entry
        if not catalog_entry.table:
            raise ValueError(
                f"Missing table name in catalog entry: {catalog_entry.to_dict()}"
            )

        return self.connector.get_fully_qualified_name(
            table_name=catalog_entry.table,
            schema_name=catalog_entry.metadata.root.schema_name,
            db_name=catalog_entry.database,
        )

    # Get records from stream

    def get_records(self, partition: Optional[dict]) -> Iterable[Dict[str, Any]]:
        """Return a generator of row-type dictionary objects.

        Args:
            partition: If provided, will read specifically from this data slice.

        Yields:
            One dict per record.

        Raises:
            NotImplementedError: If partition is passed and the stream does not
                support partitioning.
        """
        if partition:
            raise NotImplementedError(
                f"Stream '{self.name}' does not support partitioning."
            )

        for row in self.connector.connection.execute(
            sqlalchemy.text(f"SELECT * FROM {self.fully_qualified_name}")
        ):
            yield dict(row)
