import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import { Box3, Vector3, type Group } from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { API_BASE } from "../lib/api";

function Scene({ scene, resetKey }: { scene: Group; resetKey: number }) {
  const { camera, gl } = useThree();
  const { center, radius } = useMemo(() => {
    const box = new Box3().setFromObject(scene);
    const c = box.getCenter(new Vector3());
    return { center: c, radius: Math.max(box.getSize(new Vector3()).length() / 2, 0.01) };
  }, [scene]);

  useEffect(() => {
    camera.position.set(center.x + radius * 1.6, center.y + radius * 1.1, center.z + radius * 1.8);
    camera.near = radius / 100;
    camera.far = radius * 100;
    camera.updateProjectionMatrix();
    const controls = new OrbitControls(camera, gl.domElement);
    controls.target.copy(center);
    controls.enableDamping = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    controls.update();
    let raf = 0;
    const tick = () => {
      controls.update();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      controls.dispose();
    };
  }, [camera, gl, center, radius, resetKey]);

  return <primitive object={scene} />;
}

/** 3D preview of a CAD file's GLB (metres, from the cad-worker). Lazy-loaded. */
export default function GlbViewer({ token, attachmentId, label }: { token: string; attachmentId: string; label: string }) {
  const [scene, setScene] = useState<Group | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    let url: string | null = null;
    let live = true;
    // Fetch with the token header, then load from a blob URL (the token stays out of URLs).
    fetch(`${API_BASE}/projects/current/attachments/${attachmentId}/glb`, {
      headers: { "X-Project-Token": token },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.blob();
      })
      .then((b) => {
        url = URL.createObjectURL(b);
        return new GLTFLoader().loadAsync(url);
      })
      .then((gltf) => live && setScene(gltf.scene))
      .catch(() => live && setError("The 3D preview could not be loaded."));
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [token, attachmentId]);

  if (error) return <p className="notice" data-tone="warn">{error}</p>;
  return (
    <figure className="glb">
      <div className="glb__canvas" role="img" aria-label={`3D view of ${label}. Drag to rotate, scroll to zoom.`}>
        {scene ? (
          <Canvas camera={{ fov: 40 }} gl={{ alpha: true, antialias: true }} dpr={[1, 2]}>
            <hemisphereLight intensity={1.1} groundColor="#555b66" />
            <directionalLight position={[3, 5, 4]} intensity={1.4} />
            <Scene scene={scene} resetKey={resetKey} />
          </Canvas>
        ) : (
          <p role="status" className="glb__loading">
            Loading 3D preview…
          </p>
        )}
      </div>
      <figcaption className="glb__caption">
        <span className="muted small">{label}</span>
        <button type="button" className="btn btn-small" onClick={() => setResetKey((k) => k + 1)} disabled={!scene}>
          Reset view
        </button>
      </figcaption>
    </figure>
  );
}
