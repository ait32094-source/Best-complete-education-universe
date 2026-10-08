STUDY NOTES PORTAL

Admin: admin / admin123
Admin URL: /admin/login
Student URL: /login

1. python -m pip install -r requirements.txt
2. Put Google service account JSON at service_account/google-service-account.json
3. Share Drive root folder with service-account email as Editor.
4. python app.py
5. Open http://127.0.0.1:5000

The database portal.db is created automatically. PDFs are stored in Google Drive; student PDF access is streamed through the Flask server and restricted to the student's class.
