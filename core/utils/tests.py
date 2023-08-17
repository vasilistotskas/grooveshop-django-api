import os
from typing import List


def get_filename_from_url(url):
    return os.path.basename(url)


def compare_serializer_and_response(
    serializer_data: dict, response_data: dict, fields: List[str]
):
    # Extract filenames from the URLs and compare
    response_filenames = []
    serializer_filenames = []
    for field in fields:
        response_filenames.append(get_filename_from_url(response_data[field]))
        serializer_filenames.append(get_filename_from_url(serializer_data[field]))

    # Compare the data excluding the specified fields
    response_data_filtered = {
        key: value for key, value in response_data.items() if key not in fields
    }
    serializer_data_filtered = {
        key: value for key, value in serializer_data.items() if key not in fields
    }
    assert response_data_filtered == serializer_data_filtered

    # Compare the filenames
    assert response_filenames == serializer_filenames
