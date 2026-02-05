<script lang="ts">
  import { Dialog as DialogPrimitive } from 'bits-ui';

  type Props = DialogPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<DialogPrimitive.Root {...restProps}>
  {@render children?.()}
</DialogPrimitive.Root>
