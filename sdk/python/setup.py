from setuptools import setup, find_packages

setup(
    name="vos3-sdk",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[],
    extras_require={
        "httpx": ["httpx>=0.24.0"],
    },
    python_requires=">=3.9",
    description="VOS3 App Platform SDK for Python",
    author="VOS3",
    license="MIT",
)
