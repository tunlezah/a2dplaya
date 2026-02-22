"""A2DPlaya package setup."""

from setuptools import setup, find_packages

setup(
    name="a2dplaya",
    version="1.0.0",
    description="Bluetooth A2DP Sink to Chromecast/AirPlay Audio Bridge",
    author="A2DPlaya Project",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "a2dplaya": ["web/static/*"],
    },
    python_requires=">=3.8",
    install_requires=[
        "flask>=2.0",
    ],
    extras_require={
        "chromecast": ["pychromecast>=13.0"],
        "zeroconf": ["zeroconf>=0.80"],
        "full": [
            "pychromecast>=13.0",
            "zeroconf>=0.80",
        ],
    },
    entry_points={
        "console_scripts": [
            "a2dplaya=a2dplaya.__main__:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Multimedia :: Sound/Audio",
    ],
)
