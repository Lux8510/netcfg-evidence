import { Canvas, useFrame } from '@react-three/fiber';
import { useRef } from 'react';
import * as THREE from 'three';

function Field() {
  const points = useRef<THREE.Points>(null);
  const orb = useRef<THREE.Mesh>(null);
  const positions = (() => {
    const count = 900;
    const array = new Float32Array(count * 3);
    for (let index = 0; index < count; index += 1) {
      array[index * 3] = (Math.random() - 0.5) * 18;
      array[index * 3 + 1] = (Math.random() - 0.5) * 10;
      array[index * 3 + 2] = (Math.random() - 0.5) * 14;
    }
    return array;
  })();

  useFrame((state, delta) => {
    if (points.current) points.current.rotation.y += delta * 0.012;
    if (orb.current) {
      orb.current.rotation.x += delta * 0.08;
      orb.current.rotation.y += delta * 0.12;
      orb.current.position.y = Math.sin(state.clock.elapsedTime * 0.4) * 0.18;
    }
  });

  return (
    <>
      <points ref={points}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        </bufferGeometry>
        <pointsMaterial size={0.02} color="#6aa6ff" transparent opacity={0.55} sizeAttenuation depthWrite={false} />
      </points>
      <mesh ref={orb} position={[3.4, 0.6, -2]}>
        <icosahedronGeometry args={[1.05, 1]} />
        <meshBasicMaterial color="#6aa6ff" wireframe transparent opacity={0.16} />
      </mesh>
      <mesh position={[-3.6, -1.1, -3]}>
        <torusGeometry args={[0.75, 0.008, 8, 80]} />
        <meshBasicMaterial color="#3ecf8e" transparent opacity={0.18} />
      </mesh>
    </>
  );
}

export default function Scene() {
  return (
    <div className="scene pointer-events-none fixed inset-0 -z-10 opacity-70" aria-hidden>
      <Canvas dpr={[1, 1.5]} camera={{ position: [0, 0, 6], fov: 55 }} gl={{ antialias: true, alpha: true }}>
        <Field />
      </Canvas>
    </div>
  );
}
