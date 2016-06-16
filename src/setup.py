#!/usr/bin/env python

from distutils.core import setup

setup(name='asset_folder_importer',
      version='2.0',
      description='Set of programmes to handle import of items to MAM from asset folders',
      author='Andy Gallagher',
      author_email='andy.gallagher@theguardian.com',
      packages=['asset_folder_importer','asset_folder_importer.providers'],
      package_data={
            'asset_folder_importer': ['metadata_templates/*']
      },
      scripts=['asset_folder_sweeper.py','asset_folder_verify_files.py',
               'asset_folder_vsingester.py','asset_permissions.pl',
               'prelude_importer.py','premiere_get_referenced_media.py'],
      data_files=[
          ('/etc',['asset_folder_importer.cfg','footage_providers.yml'])
      ]
      )
