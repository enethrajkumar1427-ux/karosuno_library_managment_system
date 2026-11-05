"""Karasuno TEAM-12 | Automated Unit Tests for Library Management System"""
import os
import shutil
import tempfile
import unittest

from app import app, init, seed


class KarasunoLibraryTestCase(unittest.TestCase):

    def setUp(self):
        """Set up test client and initialize a fresh database before each test."""
        self.test_db_dir = tempfile.mkdtemp(prefix="karasuno_test_")
        os.environ["KARASUNO_DB"] = os.path.join(self.test_db_dir, "library.db")

        app.config["TESTING"] = True
        self.client = app.test_client()
        init()
        seed()

    def tearDown(self):
        """Remove the temporary database directory created for this test."""
        try:
            shutil.rmtree(self.test_db_dir, ignore_errors=True)
        except OSError:
            pass
   
    def test_home_route(self):
        """Test that the main frontend UI route loads successfully."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_get_books(self):
        """Test retrieving the list of seeded books."""
        response = self.client.get("/api/books")
        self.assertEqual(response.status_code, 200)
        books = response.get_json()
        self.assertIsInstance(books, list)
        self.assertGreater(len(books), 0, "Books should be seeded")

    def test_get_borrowers(self):
        """Test retrieving the list of registered borrowers."""
        response = self.client.get("/api/borrowers")
        self.assertEqual(response.status_code, 200)
        borrowers = response.get_json()
        self.assertIsInstance(borrowers, list)
        self.assertGreater(len(borrowers), 0, "Borrowers should be seeded")

    def test_get_summary(self):
        """Test library summary statistics endpoint."""
        response = self.client.get("/api/summary")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("titles", data)
        self.assertIn("borrowers", data)
        self.assertIn("active", data)

    def test_get_reports(self):
        """Test library reports endpoint."""
        response = self.client.get("/api/reports")
        self.assertEqual(response.status_code, 200)

    def test_add_new_book(self):
        """Test adding a new book via the API."""
        payload = {
            "isbn": "9780132359999",
            "title": "Clean Architecture",
            "author": "Robert C. Martin",
            "category": "Software Engineering",
            "copies": 4
        }
        response = self.client.post("/api/books", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("ok"))

    def test_add_new_borrower(self):
        """Test registering a new borrower via the API."""
        payload = {
            "borrower_name": "Tobio Kageyama",
            "roll_no": "25102A0199"
        }
        response = self.client.post("/api/borrowers", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("ok"))

    def test_settings_endpoint(self):
        """Test fetching and updating system loan duration settings."""
        res_get = self.client.get("/api/settings")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn("due_days", res_get.get_json())

        res_post = self.client.post("/api/settings", json={"due_days": 21})
        self.assertEqual(res_post.status_code, 200)
        self.assertTrue(res_post.get_json().get("ok"))


if __name__ == "__main__":
    unittest.main()