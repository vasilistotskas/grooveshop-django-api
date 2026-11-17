"""The builder's HTTP surface.

Staff routes (``StoreStaffModelPermissions``: the role in this store
decides, so ADMIN and OWNER edit and publish and STAFF only look) and one
public read of the current release. All of it is a thin layer over
``builder.services`` (the only writer) and ``builder.published``.
"""
