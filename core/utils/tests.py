import os
from typing import List


def get_filename_from_url(url):
    if url is None:
        return None
    return os.path.basename(url)


def compare_serializer_and_response(serializer_data: dict, response_data: dict, fields: List[str]):
    response_filenames = []
    serializer_filenames = []
    for field in fields:
        response_filename = get_filename_from_url(response_data[field])
        if response_filename is not None:
            response_filenames.append(response_filename)

        serializer_filename = get_filename_from_url(serializer_data[field])
        if serializer_filename is not None:
            serializer_filenames.append(serializer_filename)

    response_data_filtered = {key: value for key, value in response_data.items() if key not in fields}
    serializer_data_filtered = {key: value for key, value in serializer_data.items() if key not in fields}
    assert response_data_filtered == serializer_data_filtered

    assert response_filenames == serializer_filenames
