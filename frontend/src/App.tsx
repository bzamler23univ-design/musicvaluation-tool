import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import SongSearch from "./pages/SongSearch";
import Valuation from "./pages/Valuation";
import DataHealthPage from "./pages/DataHealth";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/search" element={<SongSearch />} />
        <Route path="/search/:id" element={<SongSearch />} />
        <Route path="/valuation" element={<Valuation />} />
        <Route path="/valuation/:id" element={<Valuation />} />
        <Route path="/health" element={<DataHealthPage />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </Layout>
  );
}
