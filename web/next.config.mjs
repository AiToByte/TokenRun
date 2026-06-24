/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
      {
        source: "/ws",
        destination: "http://localhost:8000/ws",
      },
    ];
  },
};

export default nextConfig;
