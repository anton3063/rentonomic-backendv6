<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Rentonomic</title>
  <link rel="stylesheet" href="style.css" />
  <style>
    body {
      font-family: Arial, sans-serif;
      background-color: #f9f9f9;
      margin: 0;
      padding: 2rem 1rem 3rem;
      text-align: center;
    }

    img.logo {
      width: 150px;
      max-width: 90%;
      height: auto;
      margin: 1rem auto;
    }

    .buttons {
      display: flex;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 2rem;
    }

    .buttons a {
      background-color: #28a745;
      color: white;
      padding: 0.8rem 2rem;
      text-decoration: none;
      border-radius: 6px;
      font-weight: bold;
      transition: background 0.3s;
    }

    .buttons a:hover {
      background-color: #218838;
    }

    h2 {
      margin-top: 3rem;
      font-size: 1.6rem;
    }

    #homeListings {
      display: flex;
      overflow-x: auto;
      gap: 1rem;
      padding: 1rem;
      margin-top: 1rem;
    }

    .listing-card {
      background: white;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 0.5rem;
      min-width: 220px;
      flex: 0 0 auto;
      cursor: pointer;
      transition: box-shadow 0.3s;
    }

    .listing-card:hover {
      box-shadow: 0 0 10px rgba(0,0,0,0.1);
    }

    .listing-card img {
      width: 100%;
      height: 140px;
      object-fit: cover;
      border-radius: 6px;
    }

    .listing-card h3 {
      margin: 0.5rem 0 0.2rem;
    }

    .listing-card p {
      margin: 0.2rem;
      font-size: 0.9rem;
      color: #555;
    }
  </style>
</head>
<body>
  <img src="logo.PNG" alt="Rentonomic Logo" class="logo" />
  <div class="buttons">
    <a href="list.html">List</a>
    <a href="rent.html">Rent</a>
  </div>

  <h2>Available to Rent</h2>
  <div id="homeListings">Loading...</div>

  <script src="script.js"></script>
</body>
</html>














































