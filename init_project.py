import os
import sys

def init_project():
    # Membuat seluruh subdirektori yang dibutuhkan jika script ini tidak dipanggil dari root project
    dirs = ['modules', 'config', 'hasil_scrape', 'logs', 'har', 'sessions']
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for d in dirs:
        path = os.path.join(base_dir, d)
        if not os.path.exists(path):
            os.makedirs(path)
            
    # Membuat __init__.py statis agar directory dikenali sebagai module
    init_file_modules = os.path.join(base_dir, 'modules', '__init__.py')
    if not os.path.exists(init_file_modules):
        with open(init_file_modules, 'w') as f:
            f.write('')
            
    init_file_config = os.path.join(base_dir, 'config', '__init__.py')
    if not os.path.exists(init_file_config):
        with open(init_file_config, 'w') as f:
            f.write('')

if __name__ == '__main__':
    init_project()
    print("Project directory validated/initialized.")
