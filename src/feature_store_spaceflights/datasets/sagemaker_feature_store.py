import logging
import time

import boto3
import pandas as pd
from kedro.io import AbstractDataSet
from sagemaker.feature_store.feature_group import FeatureGroup
from sagemaker.session import Session

logger = logging.getLogger(__name__)


class FeatureGroupDataSet(AbstractDataSet):
    def __init__(
        self,
        name: str,
        s3_uri: str,
        record_identifier_name: str,
        event_time_name: str,
        query: str,
        description: str = None,
    ):

        region = boto3.Session().region_name
        boto_session = boto3.Session(region_name=region)

        sagemaker_client = boto_session.client(
            service_name="sagemaker", region_name=region
        )
        featurestore_runtime = boto_session.client(
            service_name="sagemaker-featurestore-runtime", region_name=region
        )

        feature_store_session = Session(
            boto_session=boto_session,
            sagemaker_client=sagemaker_client,
            sagemaker_featurestore_runtime_client=featurestore_runtime,
        )

        iam = boto3.client("iam")
        role = iam.get_role(RoleName="AmazonSageMaker-ExecutionRole")["Role"]["Arn"]

        # you can also suffix the feature group name with pipeline git version
        self._feature_group = FeatureGroup(
            name=name, sagemaker_session=feature_store_session
        )
        self._description = description
        self._s3_uri = s3_uri
        self._role = role
        self._record_identifier_name = record_identifier_name
        self._event_time_name = event_time_name
        self._query = query

    def _wait_for_feature_group_creation_complete(self):
        status = self._feature_group.describe().get("FeatureGroupStatus")
        while status == "Creating":
            logger.info("Waiting for Feature Group Creation")
            time.sleep(5)
            status = self._feature_group.describe().get("FeatureGroupStatus")
        if status != "Created":
            raise RuntimeError(
                f"Failed to create feature group {self._feature_group.name}"
            )
        logger.info("FeatureGroup %s successfully created.", self._feature_group.name)

    def _describe(self):
        return dict(feature_group=self._feature_group)

    def _save(self, data):
        self._feature_group.load_feature_definitions(data)
        try:
            self._feature_group.create(
                description=self._description,
                s3_uri=self._s3_uri,
                record_identifier_name=self._record_identifier_name,
                event_time_feature_name=self._event_time_name,
                role_arn=self._role,
                enable_online_store=True,
            )

            self._wait_for_feature_group_creation_complete()
        except Exception as exc:
            if (
                f"Resource Already Exists: FeatureGroup with name {self._feature_group.name} already exists"
                in str(exc)
            ):
                pass
            else:
                raise

        self._feature_group.ingest(data[:10])  # just for demo purpose

    def _load(self) -> pd.DataFrame:
        query = self._feature_group.athena_query()
        print(self._query.format(table_name=query.table_name))
        query.run(
            self._query.format(table_name=query.table_name),
            output_location=f"{self._s3_uri}/query_results/",
        )
        query.wait()
        return query.as_dataframe()
