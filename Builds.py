from cx_Freeze import setup, Executable

# ADD FILES
add_files = [
]

# TARGET
target = Executable(
    script="Clinet.py",
    base="Win32GUI",
    icon="Clinet.ico",
    # uac_admin=True,
    target_name="BatchDown.exe"
)

# SETUP CX FREEZE
setup(
    name="Web Batch Downloader 网页批量下载器",
    version="1.2.2025.0904",
    description="Web Batch Downloader 网页批量下载器",
    author="Pikachu Ren",
    options={
        'build_exe': {
            "include_msvcr": True,
            'include_files': add_files,
            "packages": [
                "ttkbootstrap.utility",
                "ttkbootstrap",
            ],
        },
    },
    executables=[target],
)
