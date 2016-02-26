from setuptools import setup, find_packages

setup(
    name='airplay',

    version='0.0.5',

    description='A python client for AirPlay video',

    url='https://github.com/cnelson/python-airplay',

    author='Chris Nelson',
    author_email='cnelson@cnelson.org',

    license='Public Domain',

    classifiers=[
        'Development Status :: 3 - Alpha',

        'Intended Audience :: Developers',

        'License :: Public Domain',

        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
    ],

    keywords='airplay appletv',

    packages=find_packages(),

    install_requires=[
        'zeroconf',
        'click',
    ],

    tests_require=[
        'mock'
    ],

    test_suite='airplay.tests',

    entry_points={
        'console_scripts': [
            'airplay = airplay.cli:main'
        ]
    }
)
